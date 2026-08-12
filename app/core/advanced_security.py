# core/advanced_security.py
"""
Advanced security features including password hashing, 2FA, rate limiting,
SSO integration, user management, and password reset.
"""

import streamlit as st
import logging
import bcrypt
import pyotp
import qrcode
from io import BytesIO
import base64
from datetime import datetime, timedelta
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, List, Tuple, Any
import json
import os
import time
from functools import wraps
import re
import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# 🔐 RATE LIMITING
# ============================================================

# In-memory rate limit storage
_rate_limit_cache = {}

def _clean_rate_limit_cache():
    """Clean expired rate limit entries."""
    now = time.time()
    expired = []
    for key, data in _rate_limit_cache.items():
        if now - data['first_call'] > data['period']:
            expired.append(key)
    for key in expired:
        del _rate_limit_cache[key]


class RateLimiter:
    """
    Rate limiting for sensitive operations.
    """
    
    def __init__(self):
        self.limits_file = 'rate_limits.json'
        self._init_limits_file()
    
    def _init_limits_file(self):
        """Initialize rate limits file."""
        if not os.path.exists(self.limits_file):
            with open(self.limits_file, 'w') as f:
                json.dump({}, f)
    
    def check_limit(self, key: str, max_calls: int = 10, period: int = 60) -> bool:
        """
        Check if rate limit is exceeded.
        
        Args:
            key: Unique key for the operation (e.g., user_id + action)
            max_calls: Maximum calls allowed in the period
            period: Time period in seconds
        
        Returns:
            bool: True if limit is not exceeded
        """
        try:
            with open(self.limits_file, 'r') as f:
                limits = json.load(f)
            
            current_time = time.time()
            
            if key not in limits:
                limits[key] = {
                    'calls': 0,
                    'first_call': current_time,
                    'last_call': current_time
                }
            
            limit_data = limits[key]
            
            # Check if period has expired
            if current_time - limit_data['first_call'] > period:
                # Reset counter
                limits[key] = {
                    'calls': 1,
                    'first_call': current_time,
                    'last_call': current_time
                }
                with open(self.limits_file, 'w') as f:
                    json.dump(limits, f, indent=2)
                return True
            
            # Check if limit is exceeded
            if limit_data['calls'] >= max_calls:
                return False
            
            # Increment counter
            limits[key]['calls'] += 1
            limits[key]['last_call'] = current_time
            
            with open(self.limits_file, 'w') as f:
                json.dump(limits, f, indent=2)
            
            return True
            
        except Exception as e:
            logger.error(f"Rate limit check error: {e}")
            return True  # Allow if there's an error
    
    def get_remaining(self, key: str, max_calls: int = 10, period: int = 60) -> int:
        """
        Get remaining calls allowed.
        
        Args:
            key: Unique key for the operation
            max_calls: Maximum calls allowed
            period: Time period in seconds
        
        Returns:
            int: Remaining calls
        """
        try:
            with open(self.limits_file, 'r') as f:
                limits = json.load(f)
            
            if key not in limits:
                return max_calls
            
            current_time = time.time()
            limit_data = limits[key]
            
            if current_time - limit_data['first_call'] > period:
                return max_calls
            
            return max_calls - limit_data['calls']
            
        except Exception as e:
            logger.error(f"Get remaining calls error: {e}")
            return max_calls


# ============================================================
# 🔐 FIXED: RATE LIMITED DECORATOR (NO CIRCULAR IMPORT)
# ============================================================

def rate_limited(max_calls: int = 5, period: int = 60):
    """
    Decorator for rate limiting (in-memory version).
    
    Args:
        max_calls: Maximum calls allowed in the period
        period: Time period in seconds
    
    Usage:
        @rate_limited(max_calls=5, period=60)
        def my_function():
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get user identifier - use session state directly to avoid circular import
            user_key = 'anonymous'
            try:
                # Try to get user from session state directly
                auth_data = st.session_state.get('auth', {})
                if auth_data.get('authenticated', False):
                    user = auth_data.get('user', {})
                    if user:
                        user_key = user.get('email', 'anonymous')
            except:
                pass
            
            key = f"{user_key}:{func.__name__}"
            now = time.time()
            
            # Clean expired entries
            _clean_rate_limit_cache()
            
            # Check if key exists
            if key not in _rate_limit_cache:
                _rate_limit_cache[key] = {
                    'calls': 0,
                    'first_call': now,
                    'period': period,
                    'max_calls': max_calls
                }
            
            data = _rate_limit_cache[key]
            
            # Reset if period expired
            if now - data['first_call'] > period:
                data['calls'] = 0
                data['first_call'] = now
            
            # Check rate limit
            if data['calls'] >= max_calls:
                remaining = max_calls - data['calls']
                st.warning(f"⚠️ Rate limit exceeded. Please wait {period} seconds before trying again.")
                raise Exception(f"Rate limit exceeded. {remaining} attempts remaining.")
            
            # Increment call count
            data['calls'] += 1
            
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ============================================================
# PASSWORD HASHING
# ============================================================

class PasswordManager:
    """
    Secure password hashing and verification using bcrypt.
    """
    
    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using bcrypt.
        
        Args:
            password: Plain text password
        
        Returns:
            Hashed password string
        """
        try:
            # Generate salt and hash
            salt = bcrypt.gensalt(rounds=12)  # 12 rounds for security
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            return hashed.decode('utf-8')
        except Exception as e:
            logger.error(f"Password hashing error: {e}")
            raise ValueError("Failed to hash password")
    
    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """
        Verify a password against its hash.
        
        Args:
            password: Plain text password
            hashed: Hashed password
        
        Returns:
            bool: True if password matches
        """
        try:
            return bcrypt.checkpw(
                password.encode('utf-8'),
                hashed.encode('utf-8')
            )
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False
    
    @staticmethod
    def validate_password_strength(password: str) -> Tuple[bool, str]:
        """
        Validate password strength.
        
        Args:
            password: Password to validate
        
        Returns:
            Tuple of (is_valid, message)
        """
        if len(password) < 8:
            return False, "Password must be at least 8 characters"
        
        if not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter"
        
        if not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter"
        
        if not re.search(r'\d', password):
            return False, "Password must contain at least one number"
        
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return False, "Password must contain at least one special character"
        
        return True, "✅ Strong password"


# ============================================================
# TWO-FACTOR AUTHENTICATION (2FA)
# ============================================================

class TwoFactorAuth:
    """
    Two-factor authentication using TOTP (Time-based One-Time Password).
    """
    
    def __init__(self):
        self._init_session_state()
    
    def _init_session_state(self):
        """Initialize 2FA session state."""
        if '2fa' not in st.session_state:
            st.session_state['2fa'] = {
                'enabled': False,
                'secret': None,
                'verified': False,
                'backup_codes': []
            }
    
    def generate_secret(self) -> str:
        """
        Generate a new TOTP secret.
        
        Returns:
            Base32 encoded secret
        """
        return pyotp.random_base32()
    
    def get_totp_uri(self, secret: str, email: str) -> str:
        """
        Generate TOTP URI for QR code.
        
        Args:
            secret: TOTP secret
            email: User email
        
        Returns:
            TOTP URI string
        """
        return pyotp.totp.TOTP(secret).provisioning_uri(
            name=email,
            issuer_name="Browns Food Co - Inventory"
        )
    
    def generate_qr_code(self, secret: str, email: str) -> str:
        """
        Generate QR code for 2FA setup.
        
        Args:
            secret: TOTP secret
            email: User email
        
        Returns:
            Base64 encoded QR code image
        """
        try:
            uri = self.get_totp_uri(secret, email)
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(uri)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            return f"data:image/png;base64,{img_str}"
        except Exception as e:
            logger.error(f"QR code generation error: {e}")
            return None
    
    def verify_otp(self, secret: str, otp: str) -> bool:
        """
        Verify TOTP code.
        
        Args:
            secret: TOTP secret
            otp: One-time password
        
        Returns:
            bool: True if valid
        """
        try:
            totp = pyotp.TOTP(secret)
            return totp.verify(otp)
        except Exception as e:
            logger.error(f"OTP verification error: {e}")
            return False
    
    def generate_backup_codes(self, count: int = 10) -> List[str]:
        """
        Generate backup codes for account recovery.
        
        Args:
            count: Number of backup codes to generate
        
        Returns:
            List of backup codes
        """
        codes = []
        for _ in range(count):
            code = secrets.token_hex(4).upper()
            codes.append(f"{code[:4]}-{code[4:8]}")
        return codes
    
    def enable_2fa(self, email: str) -> Dict:
        """
        Enable 2FA for a user.
        
        Args:
            email: User email
        
        Returns:
            Dict with secret and QR code
        """
        secret = self.generate_secret()
        backup_codes = self.generate_backup_codes()
        
        st.session_state['2fa']['secret'] = secret
        st.session_state['2fa']['backup_codes'] = backup_codes
        st.session_state['2fa']['enabled'] = False  # Not verified yet
        
        qr_code = self.generate_qr_code(secret, email)
        
        return {
            'secret': secret,
            'qr_code': qr_code,
            'backup_codes': backup_codes
        }
    
    def verify_and_enable(self, otp: str) -> bool:
        """
        Verify OTP and enable 2FA.
        
        Args:
            otp: One-time password
        
        Returns:
            bool: True if successfully enabled
        """
        secret = st.session_state['2fa'].get('secret')
        if not secret:
            return False
        
        if self.verify_otp(secret, otp):
            st.session_state['2fa']['enabled'] = True
            st.session_state['2fa']['verified'] = True
            logger.info("2FA enabled successfully")
            return True
        
        return False
    
    def verify_2fa_login(self, otp: str) -> bool:
        """
        Verify 2FA during login.
        
        Args:
            otp: One-time password
        
        Returns:
            bool: True if valid
        """
        secret = st.session_state['2fa'].get('secret')
        if not secret:
            return False
        
        return self.verify_otp(secret, otp)
    
    def render_2fa_setup(self, email: str):
        """
        Render 2FA setup interface.
        """
        st.markdown("### 🔐 Two-Factor Authentication Setup")
        
        if st.session_state['2fa'].get('enabled'):
            st.success("✅ 2FA is enabled for this account")
            
            if st.button("🔄 Reset 2FA", type="secondary"):
                st.session_state['2fa'] = {
                    'enabled': False,
                    'secret': None,
                    'verified': False,
                    'backup_codes': []
                }
                st.rerun()
            return
        
        if not st.session_state['2fa'].get('secret'):
            # Generate new secret
            if st.button("📱 Set up 2FA", type="primary"):
                setup_data = self.enable_2fa(email)
                st.session_state['2fa']['secret'] = setup_data['secret']
                st.session_state['2fa']['backup_codes'] = setup_data['backup_codes']
                st.rerun()
            return
        
        # Show QR code
        secret = st.session_state['2fa']['secret']
        qr_code = self.generate_qr_code(secret, email)
        
        if qr_code:
            st.image(qr_code, width=200)
        
        st.caption(f"Secret Key: `{secret}`")
        st.caption("Scan the QR code with Google Authenticator or similar app")
        
        # Verify OTP
        otp = st.text_input("Enter the 6-digit code from your authenticator app", type="password")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Verify and Enable", type="primary"):
                if self.verify_and_enable(otp):
                    st.success("✅ 2FA enabled successfully!")
                    st.rerun()
                else:
                    st.error("❌ Invalid code. Please try again.")
        
        with col2:
            if st.button("❌ Cancel Setup"):
                st.session_state['2fa']['secret'] = None
                st.session_state['2fa']['backup_codes'] = []
                st.rerun()
        
        # Show backup codes
        if st.session_state['2fa'].get('backup_codes'):
            with st.expander("📋 Backup Codes (Save These!)"):
                st.warning("⚠️ Store these backup codes in a safe place. They can be used to access your account if you lose your authenticator app.")
                codes = st.session_state['2fa']['backup_codes']
                for code in codes:
                    st.code(code)
    
    def render_2fa_login(self):
        """
        Render 2FA login form.
        """
        st.markdown("### 📱 Two-Factor Authentication")
        st.caption("Enter the 6-digit code from your authenticator app")
        
        otp = st.text_input("Authentication Code", type="password", placeholder="123456")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Verify", type="primary"):
                if self.verify_2fa_login(otp):
                    st.session_state['2fa']['verified'] = True
                    st.success("✅ 2FA verified!")
                    return True
                else:
                    st.error("❌ Invalid code. Please try again.")
        
        with col2:
            if st.button("🔑 Use Backup Code"):
                backup_code = st.text_input("Enter backup code", type="password")
                if backup_code in st.session_state['2fa'].get('backup_codes', []):
                    st.session_state['2fa']['verified'] = True
                    # Remove used backup code
                    st.session_state['2fa']['backup_codes'].remove(backup_code)
                    st.success("✅ Backup code accepted!")
                    return True
                else:
                    st.error("❌ Invalid backup code")
        
        return False


# ============================================================
# SSO/OAUTH INTEGRATION (Simplified)
# ============================================================

class SSOAuth:
    """
    Single Sign-On / OAuth integration.
    """
    
    # This is a simplified version. In production, use authlib or similar.
    
    @staticmethod
    def google_login():
        """
        Google OAuth login (placeholder).
        """
        st.info("🔑 Google SSO coming soon!")
        # In production, implement OAuth flow
        pass
    
    @staticmethod
    def microsoft_login():
        """
        Microsoft OAuth login (placeholder).
        """
        st.info("🔑 Microsoft SSO coming soon!")
        # In production, implement OAuth flow
        pass


# ============================================================
# USER MANAGEMENT
# ============================================================

class UserManager:
    """
    User management system with CRUD operations.

    Dual-backend: Supabase primary, users.json fallback -- same pattern as
    cheese_data_access.py. This matters more here than almost anywhere else
    in the app: users.json lives on Streamlit Cloud's EPHEMERAL filesystem,
    wiped on every redeploy/container restart/sleep-wake cycle. Since
    users.json is gitignored, a fresh container has NO users.json at all --
    _init_users_file() recreates it from scratch with only the default
    admin account, silently discarding every user created since the last
    restart. That was the actual cause of "create a user, works, then next
    login says invalid" -- the account genuinely no longer existed.
    """
    
    def __init__(self, supabase_client: Optional[Any] = None):
        self.supabase_client = supabase_client
        self.users_file = 'users.json'
        self._init_users_file()
    
    def _init_users_file(self):
        """Initialize users file."""
        if not os.path.exists(self.users_file):
            # Create default admin user
            default_users = {
                'admin@browns.com': {
                    'password': PasswordManager.hash_password('Admin123!'),
                    'role': 'admin',
                    'name': 'System Administrator',
                    'email_verified': True,
                    '2fa_enabled': False,
                    'created': datetime.now().isoformat(),
                    'last_login': None
                }
            }
            with open(self.users_file, 'w') as f:
                json.dump(default_users, f, indent=2)
    
    def get_users(self) -> Dict:
        """Get all users. Supabase first (survives restarts), users.json
        only as a fallback if Supabase is unreachable -- NOT merged with
        Supabase results, since a stale local file re-adding a deleted
        user would be worse than briefly showing none."""
        if self.supabase_client:
            try:
                result = self.supabase_client.table("app_users").select("*").execute()
                users = {}
                for row in result.data:
                    users[row["email"]] = {
                        'password': row.get('password_hash'),
                        'role': row.get('role'),
                        'name': row.get('name'),
                        'email_verified': row.get('email_verified', False),
                        '2fa_enabled': row.get('twofa_enabled', False),
                        'created': row.get('created_at'),
                        'last_login': row.get('last_login'),
                    }
                return users
            except Exception as e:
                logger.error(f"Supabase get_users failed, falling back to users.json "
                             f"(ephemeral on Streamlit Cloud): {e}")
        try:
            with open(self.users_file, 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def get_user(self, email: str) -> Optional[Dict]:
        """Get a specific user."""
        users = self.get_users()
        return users.get(email)
    
    def create_user(self, email: str, password: str, role: str, name: str) -> bool:
        """
        Create a new user.
        
        Args:
            email: User email
            password: User password
            role: User role (admin, manager, user, viewer)
            name: User name
        
        Returns:
            bool: True if created successfully
        """
        try:
            users = self.get_users()
            
            # Check if user exists
            if email in users:
                return False
            
            # Validate email
            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
                return False
            
            # Validate password strength
            is_valid, msg = PasswordManager.validate_password_strength(password)
            if not is_valid:
                return False
            
            # Create user
            password_hash = PasswordManager.hash_password(password)
            created_at = datetime.now().isoformat()
            users[email] = {
                'password': password_hash,
                'role': role,
                'name': name,
                'email_verified': False,
                '2fa_enabled': False,
                'created': created_at,
                'last_login': None
            }

            if self.supabase_client:
                try:
                    self.supabase_client.table("app_users").insert({
                        "email": email, "password_hash": password_hash, "role": role,
                        "name": name, "email_verified": False, "twofa_enabled": False,
                        "created_at": created_at, "last_login": None,
                    }).execute()
                    logger.info(f"User created in Supabase: {email} ({role})")
                    return True
                except Exception as e:
                    logger.error(f"Supabase create_user failed for {email}, falling back "
                                 f"to users.json (ephemeral on Streamlit Cloud): {e}")

            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)

            logger.info(f"User created (local fallback): {email} ({role})")
            return True
            
        except Exception as e:
            logger.error(f"User creation error: {e}")
            return False
    
    _LOCAL_TO_SUPABASE_FIELD = {
        'password': 'password_hash', 'role': 'role', 'name': 'name',
        'email_verified': 'email_verified', '2fa_enabled': 'twofa_enabled',
        'last_login': 'last_login',
    }

    def update_user(self, email: str, **kwargs) -> bool:
        """
        Update user information.
        
        Args:
            email: User email
            **kwargs: Fields to update
        
        Returns:
            bool: True if updated successfully
        """
        try:
            users = self.get_users()
            
            if email not in users:
                return False
            
            update_row = {}
            for key, value in kwargs.items():
                if key in users[email]:
                    if key == 'password':
                        hashed = PasswordManager.hash_password(value)
                        users[email][key] = hashed
                        update_row['password_hash'] = hashed
                    else:
                        users[email][key] = value
                        update_row[self._LOCAL_TO_SUPABASE_FIELD.get(key, key)] = value

            if self.supabase_client:
                try:
                    self.supabase_client.table("app_users").update(update_row).eq("email", email).execute()
                    logger.info(f"User updated in Supabase: {email}")
                    return True
                except Exception as e:
                    logger.error(f"Supabase update_user failed for {email}, falling back "
                                 f"to users.json (ephemeral on Streamlit Cloud): {e}")

            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)
            
            logger.info(f"User updated (local fallback): {email}")
            return True
            
        except Exception as e:
            logger.error(f"User update error: {e}")
            return False
    
    def delete_user(self, email: str) -> bool:
        """
        Delete a user.
        
        Args:
            email: User email to delete
        
        Returns:
            bool: True if deleted successfully
        """
        try:
            users = self.get_users()
            
            if email not in users:
                return False
            
            # Don't allow deleting the last admin
            admins = [u for u, d in users.items() if d.get('role') == 'admin']
            if len(admins) <= 1 and users[email].get('role') == 'admin':
                return False
            
            del users[email]

            if self.supabase_client:
                try:
                    self.supabase_client.table("app_users").delete().eq("email", email).execute()
                    logger.info(f"User deleted from Supabase: {email}")
                    return True
                except Exception as e:
                    logger.error(f"Supabase delete_user failed for {email}, falling back "
                                 f"to users.json (ephemeral on Streamlit Cloud): {e}")

            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)
            
            logger.info(f"User deleted (local fallback): {email}")
            return True
            
        except Exception as e:
            logger.error(f"User deletion error: {e}")
            return False
    
    def toggle_2fa(self, email: str) -> bool:
        """Toggle 2FA for a user. Delegates to update_user so both
        backends stay in sync through one code path, instead of
        duplicating the write logic here."""
        users = self.get_users()
        if email not in users:
            return False
        current = users[email].get('2fa_enabled', False)
        return self.update_user(email, **{'2fa_enabled': not current})
    
    def render_user_management(self):
        """
        Render user management dashboard (Admin only).
        """
        st.markdown("### 👤 User Management")
        
        # Import AuthManager here to avoid circular import
        from core.security import AuthManager
        auth = AuthManager()
        if not auth.is_authenticated or auth.current_role != 'admin':
            st.warning("🔒 This section is restricted to administrators")
            return
        
        users = self.get_users()
        
        # Display users
        st.markdown("#### 📋 Current Users")
        
        user_data = []
        for email, data in users.items():
            user_data.append({
                'Email': email,
                'Name': data.get('name', ''),
                'Role': data.get('role', 'viewer').title(),
                '2FA Enabled': '✅' if data.get('2fa_enabled') else '❌',
                'Verified': '✅' if data.get('email_verified') else '❌',
                'Created': data.get('created', '')[:10]
            })
        
        if user_data:
            st.dataframe(pd.DataFrame(user_data), use_container_width=True, hide_index=True)
        
        # Stats
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("👤 Total Users", len(users))
        with col2:
            admins = len([u for u in users.values() if u.get('role') == 'admin'])
            st.metric("👑 Admins", admins)
        with col3:
            twofa_users = len([u for u in users.values() if u.get('2fa_enabled')])
            st.metric("🔐 2FA Enabled", twofa_users)
        
        # Add user form
        st.markdown("---")
        st.markdown("#### ➕ Add New User")
        
        col1, col2 = st.columns(2)
        
        with col1:
            new_email = st.text_input("Email Address", placeholder="user@example.com")
            new_name = st.text_input("Full Name", placeholder="John Doe")
        
        with col2:
            new_password = st.text_input("Password", type="password", placeholder="••••••••")
            new_role = st.selectbox("Role", ['viewer', 'user', 'manager', 'admin'])
        
        # Show password requirements
        with st.expander("🔑 Password Requirements"):
            st.markdown("""
            - Minimum 8 characters
            - At least one uppercase letter
            - At least one lowercase letter
            - At least one number
            - At least one special character (!@#$%^&*)
            """)
        
        if st.button("👤 Create User", type="primary"):
            if not new_email or not new_password or not new_name:
                st.error("❌ Please fill in all fields")
            else:
                success = self.create_user(new_email, new_password, new_role, new_name)
                if success:
                    st.success(f"✅ User {new_email} created successfully!")
                    st.rerun()
                else:
                    st.error("❌ Failed to create user. Email may already exist or password is weak.")
        
        # User actions
        st.markdown("---")
        st.markdown("#### 🔧 User Actions")
        
        selected_user = st.selectbox(
            "Select user to manage",
            list(users.keys()) if users else ["No users"]
        )
        
        if selected_user and selected_user != "No users":
            col1, col2, col3 = st.columns(3)
            
            with col1:
                # Real 2FA setup (QR code / authenticator app) isn't built
                # yet -- render_2fa_setup exists but nothing calls it. Toggling
                # this "on" from here put an account into a state where login
                # demands a code no one could generate. Only "off" is safe
                # until real setup exists.
                selected_user_2fa = users.get(selected_user, {}).get('2fa_enabled', False)
                if selected_user_2fa:
                    if st.button("🔓 Turn Off 2FA", use_container_width=True):
                        success = self.toggle_2fa(selected_user)
                        if success:
                            st.success(f"✅ 2FA turned off for {selected_user}")
                            st.rerun()
                        else:
                            st.error("❌ Failed to turn off 2FA")
                else:
                    st.button(
                        "🔐 Toggle 2FA", use_container_width=True, disabled=True,
                        help="2FA setup isn't available yet — turning this on "
                             "would lock the account out at next login.",
                    )
            
            with col2:
                if st.button("👤 Reset Password", use_container_width=True):
                    st.session_state._reset_password_for = selected_user
                    st.info(f"📧 Password reset link sent to {selected_user}")
            
            with col3:
                if st.button("🗑️ Delete User", type="secondary", use_container_width=True):
                    if selected_user != auth.current_user['email']:
                        if st.button("⚠️ Confirm Delete", type="primary"):
                            success = self.delete_user(selected_user)
                            if success:
                                st.success(f"✅ User {selected_user} deleted")
                                st.rerun()
                            else:
                                st.error("❌ Failed to delete user")
                    else:
                        st.warning("⚠️ Cannot delete your own account")


# ============================================================
# PASSWORD RESET
# ============================================================

class PasswordReset:
    """
    Password reset functionality with email.
    """
    
    def __init__(self):
        self.reset_file = 'password_resets.json'
        self._init_reset_file()
    
    def _init_reset_file(self):
        """Initialize password reset file."""
        if not os.path.exists(self.reset_file):
            with open(self.reset_file, 'w') as f:
                json.dump({}, f)
    
    def request_reset(self, email: str) -> Dict:
        """
        Request password reset for a user.
        
        Args:
            email: User email
        
        Returns:
            Dict with success status and message
        """
        try:
            # Check if user exists
            user_manager = UserManager()
            users = user_manager.get_users()
            
            if email not in users:
                return {
                    'success': False,
                    'message': 'Email not found in our system'
                }
            
            # Generate reset token
            token = secrets.token_urlsafe(32)
            expires = (datetime.now() + timedelta(hours=1)).isoformat()
            
            # Store reset request
            with open(self.reset_file, 'r') as f:
                resets = json.load(f)
            
            resets[token] = {
                'email': email,
                'created': datetime.now().isoformat(),
                'expires': expires,
                'used': False
            }
            
            with open(self.reset_file, 'w') as f:
                json.dump(resets, f, indent=2)
            
            # In production, send email here
            self._send_reset_email(email, token)
            
            logger.info(f"Password reset requested for: {email}")
            return {
                'success': True,
                'message': 'Password reset link sent to your email'
            }
            
        except Exception as e:
            logger.error(f"Password reset request error: {e}")
            return {
                'success': False,
                'message': f'Password reset failed: {str(e)}'
            }
    
    def reset_password(self, token: str, new_password: str) -> bool:
        """
        Reset password using token.
        
        Args:
            token: Reset token
            new_password: New password
        
        Returns:
            bool: True if password reset successful
        """
        try:
            with open(self.reset_file, 'r') as f:
                resets = json.load(f)
            
            if token not in resets:
                return False
            
            reset_data = resets[token]
            
            # Check if expired
            expires = datetime.fromisoformat(reset_data['expires'])
            if datetime.now() > expires:
                return False
            
            # Check if used
            if reset_data.get('used', False):
                return False
            
            # Validate password strength
            is_valid, msg = PasswordManager.validate_password_strength(new_password)
            if not is_valid:
                return False
            
            # Update password
            user_manager = UserManager()
            email = reset_data['email']
            
            success = user_manager.update_user(email, password=new_password)
            
            if success:
                # Mark token as used
                reset_data['used'] = True
                resets[token] = reset_data
                with open(self.reset_file, 'w') as f:
                    json.dump(resets, f, indent=2)
                
                logger.info(f"Password reset successful for: {email}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Password reset error: {e}")
            return False
    
    def _send_reset_email(self, email: str, token: str):
        """
        Send password reset email (placeholder).
        
        In production, implement actual email sending.
        """
        # This is a placeholder - implement with your email provider
        reset_link = f"http://localhost:8501/?reset_token={token}"
        logger.info(f"Password reset link for {email}: {reset_link}")
    
    def render_password_reset(self):
        """
        Render password reset interface.
        """
        st.markdown("### 🔑 Password Reset")
        
        # Check if token is provided
        params = st.query_params
        if 'reset_token' in params:
            token = params['reset_token']
            st.markdown("#### Enter your new password")
            
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            if st.button("🔑 Reset Password", type="primary"):
                if new_password != confirm_password:
                    st.error("❌ Passwords do not match")
                elif len(new_password) < 8:
                    st.error("❌ Password must be at least 8 characters")
                else:
                    success = self.reset_password(token, new_password)
                    if success:
                        st.success("✅ Password reset successful! You can now login.")
                        st.query_params.clear()
                    else:
                        st.error("❌ Invalid or expired reset token")
        else:
            # Request reset
            email = st.text_input("Email Address", placeholder="user@example.com")
            
            if st.button("📧 Send Reset Link", type="primary"):
                if email:
                    result = self.request_reset(email)
                    if result['success']:
                        st.success(result['message'])
                    else:
                        st.error(result['message'])
                else:
                    st.warning("⚠️ Please enter your email address")