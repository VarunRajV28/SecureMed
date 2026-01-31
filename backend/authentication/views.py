from datetime import timedelta
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
import pyotp
import jwt
from django.conf import settings

from .serializers import (
    UserRegistrationSerializer,
    UserLoginSerializer,
    MFASetupSerializer,
    MFAVerifySerializer,
    MFALoginSerializer,
    UserSerializer
)

User = get_user_model()

# Constants
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15


def get_tokens_for_user(user):
    """
    Generate JWT tokens for a user.
    """
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


def generate_temp_token(user):
    """
    Generate a temporary token for MFA flow.
    This token is short-lived and only used to verify MFA.
    """
    payload = {
        'user_id': user.id,
        'exp': timezone.now() + timedelta(minutes=5),
        'type': 'mfa_temp'
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def verify_temp_token(token):
    """
    Verify and decode temporary MFA token.
    Returns user_id if valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        if payload.get('type') == 'mfa_temp':
            return payload.get('user_id')
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    return None


@api_view(['POST'])
@permission_classes([AllowAny])
def register_view(request):
    """
    User registration endpoint with invite-only access.
    POST /api/auth/register
    
    Request body:
    {
        "username": "string",
        "email": "string",
        "password": "string",
        "password_confirm": "string",
        "role": "patient|provider|admin" (optional, defaults to patient),
        "token": "uuid-string" (invitation token),
        "captcha_token": true (CAPTCHA verification)
    }
    """
    # Capture IP address for audit logging
    ip_address = request.META.get('REMOTE_ADDR', 'Unknown')
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip_address = x_forwarded_for.split(',')[0].strip()
    
    # Log registration attempt
    print("\n" + "="*70)
    print("REGISTRATION ATTEMPT - AUDIT LOG")
    print("="*70)
    print(f"Timestamp: {timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"IP Address: {ip_address}")
    print(f"Email: {request.data.get('email', 'Not provided')}")
    print(f"Username: {request.data.get('username', 'Not provided')}")
    
    # Validate invitation token before proceeding
    token = request.data.get('token')
    if not token:
        print(f"Status: FAILED - No invitation token provided")
        print("="*70 + "\n")
        return Response({
            'error': 'Invitation token is required for registration'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        from .models import Invitation
        invitation = Invitation.objects.get(token=token)
        
        # Check if invitation is valid
        if invitation.is_used:
            print(f"Status: FAILED - Invitation already used")
            print(f"Used by: {invitation.used_by.username if invitation.used_by else 'Unknown'}")
            print(f"Used at: {invitation.used_at}")
            print("="*70 + "\n")
            return Response({
                'error': 'This invitation has already been used'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if timezone.now() > invitation.expires_at:
            print(f"Status: FAILED - Invitation expired")
            print(f"Expired at: {invitation.expires_at}")
            print("="*70 + "\n")
            return Response({
                'error': 'This invitation has expired'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify email matches invitation
        if request.data.get('email') != invitation.email:
            print(f"Status: FAILED - Email mismatch")
            print(f"Expected: {invitation.email}")
            print(f"Provided: {request.data.get('email')}")
            print("="*70 + "\n")
            return Response({
                'error': 'Email does not match invitation'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        print(f"Invitation Token: Valid")
        print(f"Invited by: {invitation.sent_by.username}")
        
    except Invitation.DoesNotExist:
        print(f"Status: FAILED - Invalid invitation token")
        print("="*70 + "\n")
        return Response({
            'error': 'Invalid invitation token'
        }, status=status.HTTP_404_NOT_FOUND)
    
    # Proceed with registration
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        
        # Mark invitation as used
        invitation.mark_as_used(user)
        
        print(f"Status: SUCCESS - User registered")
        print(f"User ID: {user.id}")
        print(f"Invitation marked as used")
        print("="*70 + "\n")
        
        return Response({
            'message': 'User registered successfully',
            'user': UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)
    
    print(f"Status: FAILED - Validation errors")
    print(f"Errors: {serializer.errors}")
    print("="*70 + "\n")
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """
    User login endpoint with lockout and MFA support.
    POST /api/auth/login
    
    Request body:
    {
        "username": "string",
        "password": "string"
    }
    
    Response (no MFA):
    {
        "access": "token",
        "refresh": "token",
        "user": {...}
    }
    
    Response (MFA required):
    {
        "mfa_required": true,
        "temp_token": "token"
    }
    """
    serializer = UserLoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    username_or_email = serializer.validated_data['username']
    password = serializer.validated_data['password']
    
    # Try to find user by username or email
    try:
        if '@' in username_or_email:
            user = User.objects.get(email=username_or_email)
        else:
            user = User.objects.get(username=username_or_email)
    except User.DoesNotExist:
        return Response({
            'error': 'Invalid credentials'
        }, status=status.HTTP_401_UNAUTHORIZED)
    
    # Check if account is locked
    if user.locked_until and user.locked_until > timezone.now():
        remaining_time = (user.locked_until - timezone.now()).seconds // 60
        return Response({
            'error': f'Account is locked. Try again in {remaining_time} minutes.'
        }, status=status.HTTP_403_FORBIDDEN)
    
    # Reset lockout if time has passed
    if user.locked_until and user.locked_until <= timezone.now():
        user.locked_until = None
        user.failed_login_attempts = 0
        user.save()
    
    # Verify password
    if not user.check_password(password):
        # Increment failed attempts
        user.failed_login_attempts += 1
        
        # Lock account if max attempts exceeded
        if user.failed_login_attempts > MAX_FAILED_ATTEMPTS:
            user.locked_until = timezone.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            user.save()
            return Response({
                'error': f'Too many failed attempts. Account locked for {LOCKOUT_DURATION_MINUTES} minutes.'
            }, status=status.HTTP_403_FORBIDDEN)
        
        user.save()
        remaining_attempts = MAX_FAILED_ATTEMPTS - user.failed_login_attempts + 1
        return Response({
            'error': f'Invalid credentials. {remaining_attempts} attempts remaining.'
        }, status=status.HTTP_401_UNAUTHORIZED)
    
    # Password is correct - reset failed attempts
    user.failed_login_attempts = 0
    user.locked_until = None
    user.save()
    
    # Check if MFA is enabled
    if user.mfa_enabled:
        # Return temp token for MFA verification
        temp_token = generate_temp_token(user)
        return Response({
            'mfa_required': True,
            'temp_token': temp_token
        }, status=status.HTTP_200_OK)
    
    # No MFA - return tokens immediately
    tokens = get_tokens_for_user(user)
    return Response({
        'access': tokens['access'],
        'refresh': tokens['refresh'],
        'user': UserSerializer(user).data
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mfa_setup_view(request):
    """
    MFA setup endpoint - generates TOTP secret and provisioning URI.
    POST /api/auth/mfa/setup
    
    Response:
    {
        "secret": "base32_secret",
        "provisioning_uri": "otpauth://..."
    }
    """
    user = request.user
    
    # Generate new TOTP secret
    secret = pyotp.random_base32()
    
    # Save secret to user (not enabled yet)
    user.mfa_secret = secret
    user.save()
    
    # Generate provisioning URI for QR code
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name=user.email,
        issuer_name='SecureMed'
    )
    
    return Response({
        'secret': secret,
        'provisioning_uri': provisioning_uri
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mfa_verify_view(request):
    """
    MFA verification endpoint - verifies TOTP code and enables MFA.
    POST /api/auth/mfa/verify
    
    Request body:
    {
        "otp": "123456"
    }
    """
    serializer = MFAVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    user = request.user
    otp = serializer.validated_data['otp']
    
    if not user.mfa_secret:
        return Response({
            'error': 'MFA not set up. Call /mfa/setup first.'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Verify OTP
    totp = pyotp.TOTP(user.mfa_secret)
    if totp.verify(otp, valid_window=1):
        # Enable MFA
        user.mfa_enabled = True
        user.save()
        return Response({
            'message': 'MFA enabled successfully'
        }, status=status.HTTP_200_OK)
    
    return Response({
        'error': 'Invalid OTP code'
    }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def mfa_login_view(request):
    """
    MFA login finalization endpoint - verifies OTP and returns JWT tokens.
    POST /api/auth/mfa/login
    
    Request body:
    {
        "temp_token": "string",
        "otp": "123456"
    }
    
    Response:
    {
        "access": "token",
        "refresh": "token",
        "user": {...}
    }
    """
    serializer = MFALoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    temp_token = serializer.validated_data['temp_token']
    otp = serializer.validated_data['otp']
    
    # Verify temp token
    user_id = verify_temp_token(temp_token)
    if not user_id:
        return Response({
            'error': 'Invalid or expired temporary token'
        }, status=status.HTTP_401_UNAUTHORIZED)
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({
            'error': 'User not found'
        }, status=status.HTTP_404_NOT_FOUND)
    
    if not user.mfa_enabled or not user.mfa_secret:
        return Response({
            'error': 'MFA not enabled for this user'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Verify OTP
    totp = pyotp.TOTP(user.mfa_secret)
    if totp.verify(otp, valid_window=1):
        # OTP valid - return JWT tokens
        tokens = get_tokens_for_user(user)
        return Response({
            'access': tokens['access'],
            'refresh': tokens['refresh'],
            'user': UserSerializer(user).data
        }, status=status.HTTP_200_OK)
    
    return Response({
        'error': 'Invalid OTP code'
    }, status=status.HTTP_401_UNAUTHORIZED)


# ============================================
# RBAC Test Endpoints
# ============================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def doctor_dashboard_test(request):
    """
    Test endpoint for doctor role.
    GET /api/doctor/test-dashboard/
    
    Should only be accessible by users with 'provider' role.
    """
    return Response({
        'message': 'Welcome Doctor',
        'user': request.user.username,
        'role': request.user.role
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def patient_dashboard_test(request):
    """
    Test endpoint for patient role.
    GET /api/patient/test-dashboard/
    
    Should only be accessible by users with 'patient' role.
    """
    return Response({
        'message': 'Welcome Patient',
        'user': request.user.username,
        'role': request.user.role
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_dashboard_test(request):
    """
    Test endpoint for admin role.
    GET /api/admin/test-dashboard/
    
    Should only be accessible by users with 'admin' role.
    """
    return Response({
        'message': 'Welcome Admin',
        'user': request.user.username,
        'role': request.user.role
    }, status=status.HTTP_200_OK)


# ============================================
# Session Security - Logout
# ============================================

from rest_framework.views import APIView

class LogoutView(APIView):
    """
    Logout endpoint - blacklists the refresh token to invalidate it.
    POST /api/auth/logout/
    
    Request body:
    {
        "refresh": "refresh_token_string"
    }
    
    Response:
    {
        "message": "Successfully logged out"
    }
    """
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({"message": "Successfully logged out"}, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response({"error": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)


# ============================================
# RBAC Testing - Admin Only Test View
# ============================================

from rest_framework.decorators import permission_classes
from rest_framework.permissions import IsAuthenticated

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_test_view(request):
    """
    Test endpoint to verify RBAC is working.
    Only users with 'Admin' role should be able to access this.
    
    GET /api/auth/admin-test/
    
    Response:
    {
        "message": "Admin access granted",
        "user": "username",
        "role": "Admin"
    }
    """
    # Check if user has Admin role
    if not request.user.groups.filter(name='Admin').exists():
        return Response(
            {"error": "Access denied. Admin role required."},
            status=status.HTTP_403_FORBIDDEN
        )
    
    return Response({
        "message": "Admin access granted",
        "user": request.user.username,
        "role": "Admin"
    }, status=status.HTTP_200_OK)


# ============================================
# Invitation System - Invite-Only Registration
# ============================================

from .models import Invitation

class SendInviteView(APIView):
    """
    Admin-only endpoint to send registration invitations.
    
    POST /api/auth/invite/send/
    
    Request body:
    {
        "email": "newuser@example.com"
    }
    
    Response:
    {
        "message": "Invitation sent successfully",
        "invitation": {
            "email": "newuser@example.com",
            "token": "uuid-string",
            "expires_at": "2024-02-02T10:00:00Z",
            "registration_link": "http://localhost:3000/register?token=uuid"
        }
    }
    """
    permission_classes = (IsAuthenticated,)
    
    def post(self, request):
        # Check if user has Admin role
        if not request.user.groups.filter(name='Admin').exists():
            return Response(
                {"error": "Access denied. Admin role required."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        email = request.data.get('email')
        
        if not email:
            return Response(
                {"error": "Email is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user already exists
        if User.objects.filter(email=email).exists():
            return Response(
                {"error": "User with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if there's already a valid invitation for this email
        existing_invitation = Invitation.objects.filter(
            email=email,
            is_used=False,
            expires_at__gt=timezone.now()
        ).first()
        
        if existing_invitation:
            return Response(
                {
                    "error": "An active invitation already exists for this email",
                    "token": str(existing_invitation.token),
                    "expires_at": existing_invitation.expires_at
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create new invitation
        invitation = Invitation.objects.create(
            email=email,
            sent_by=request.user
        )
        
        # Generate registration link
        registration_link = f"http://localhost:3000/register?token={invitation.token}"
        
        # Mock email sending - print to console
        print("\n" + "="*70)
        print("INVITATION EMAIL (Mock)")
        print("="*70)
        print(f"To: {email}")
        print(f"From: {request.user.email}")
        print(f"Subject: You're invited to join SecureMed")
        print("\nMessage:")
        print(f"Hello,")
        print(f"\nYou have been invited to join SecureMed by {request.user.get_full_name()}.")
        print(f"\nPlease click the link below to complete your registration:")
        print(f"{registration_link}")
        print(f"\nThis invitation will expire in 48 hours.")
        print(f"Expires at: {invitation.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("="*70 + "\n")
        
        return Response({
            "message": "Invitation sent successfully",
            "invitation": {
                "email": invitation.email,
                "token": str(invitation.token),
                "expires_at": invitation.expires_at,
                "registration_link": registration_link
            }
        }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_invite_view(request):
    """
    Verify if an invitation token is valid.
    
    POST /api/auth/invite/verify/
    
    Request body:
    {
        "token": "uuid-string"
    }
    
    Response (valid):
    {
        "valid": true,
        "email": "newuser@example.com",
        "message": "Invitation is valid"
    }
    
    Response (invalid):
    {
        "valid": false,
        "message": "Invitation has expired / already been used / does not exist"
    }
    """
    token = request.data.get('token')
    
    if not token:
        return Response(
            {
                "valid": False,
                "message": "Token is required"
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        invitation = Invitation.objects.get(token=token)
        
        if invitation.is_used:
            return Response({
                "valid": False,
                "message": "This invitation has already been used"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if timezone.now() > invitation.expires_at:
            return Response({
                "valid": False,
                "message": "This invitation has expired"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Invitation is valid
        return Response({
            "valid": True,
            "email": invitation.email,
            "message": "Invitation is valid"
        }, status=status.HTTP_200_OK)
        
    except Invitation.DoesNotExist:
        return Response({
            "valid": False,
            "message": "Invalid invitation token"
        }, status=status.HTTP_404_NOT_FOUND)

