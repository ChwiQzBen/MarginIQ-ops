"""
scripts/_migration_db.py
==========================
Elevated-privilege Supabase connection for one-time migration/admin
scripts ONLY -- never import this from app code. The app's own
init_supabase() (app/core/supabase_client.py) uses the anon key and
correctly stays RLS-constrained; this uses the service role key, which
bypasses RLS entirely. That's appropriate for a one-time script running
outside any user's session, and never appropriate for anything the app
itself calls on a user's behalf.

Reads credentials from environment variables, loaded from a local .env
file if present (python-dotenv). The key never gets hardcoded here, never
gets pasted into a script, and never needs to leave your machine.

Required in your local .env (create this file yourself -- never commit it):
    SUPABASE_URL=<your project URL>
    SUPABASE_SERVICE_ROLE_KEY=<the service_role secret, NOT the anon key>

Find the service_role key: Supabase dashboard -> Project Settings -> API
-> Project API keys -> service_role (marked secret).
"""
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def init_supabase_admin():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. Set these in a "
            "local .env file in the repo root (add .env to .gitignore if it "
            "isn't already) or as environment variables before running this script."
        )
    return create_client(url, key)
