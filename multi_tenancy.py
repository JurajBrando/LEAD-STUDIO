"""
multi_tenancy.py - Multi-tenancy and team support.

This module handles:
- Organization management
- User accounts with roles (owner/admin/member)
- Team-based data isolation
- Permission checking
"""
import os, sys, json, uuid, hashlib
from typing import List, Dict, Optional
from lead_studio_schema import connect


def create_organization(name: str, slug: str = None) -> str:
    """Create a new organization."""
    con = connect()
    org_id = f"org_{uuid.uuid4().hex[:12]}"

    if not slug:
        slug = name.lower().replace(" ", "-")

    con.execute("""
        INSERT INTO organizations (id, name, slug)
        VALUES (?, ?, ?)
    """, [org_id, name, slug])

    con.close()
    return org_id


def create_user(email: str, name: str = "", password: str = "", role: str = "member",
                organization_id: str = "default") -> str:
    """Create a new user."""
    con = connect()
    user_id = f"user_{uuid.uuid4().hex[:12]}"

    # Hash password
    password_hash = hashlib.sha256(password.encode()).hexdigest() if password else ""

    con.execute("""
        INSERT INTO users (id, email, name, password_hash, role, organization_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [user_id, email, name, password_hash, role, organization_id])

    con.close()
    return user_id


def get_organization(org_id: str) -> Optional[Dict]:
    """Get organization details."""
    con = connect()

    org = con.execute("""
        SELECT * FROM organizations WHERE id = ?
    """, [org_id]).fetchone()

    con.close()

    if not org:
        return None

    return {
        "id": org[0],
        "name": org[1],
        "slug": org[2],
        "created_at": org[3],
        "is_active": org[4]
    }


def get_user(user_id: str) -> Optional[Dict]:
    """Get user details."""
    con = connect()

    user = con.execute("""
        SELECT * FROM users WHERE id = ?
    """, [user_id]).fetchone()

    con.close()

    if not user:
        return None

    return {
        "id": user[0],
        "email": user[1],
        "name": user[2],
        "role": user[4],
        "organization_id": user[5],
        "created_at": user[6],
        "last_login_at": user[7]
    }


def get_users_by_organization(org_id: str) -> List[Dict]:
    """Get all users in an organization."""
    con = connect()

    users = con.execute("""
        SELECT * FROM users WHERE organization_id = ?
    """, [org_id]).fetchall()

    con.close()

    return [
        {
            "id": u[0],
            "email": u[1],
            "name": u[2],
            "role": u[4],
            "created_at": u[6],
            "last_login_at": u[7]
        }
        for u in users
    ]


def check_permission(user_id: str, permission: str) -> bool:
    """Check if a user has a specific permission."""
    user = get_user(user_id)
    if not user:
        return False

    role = user["role"]

    # Role-based permissions
    permissions = {
        "owner": ["all"],
        "admin": ["manage_users", "manage_campaigns", "view_analytics", "send_emails"],
        "member": ["view_leads", "view_analytics"]
    }

    if role == "owner":
        return True

    if permission in permissions.get(role, []):
        return True

    return False


def update_user_role(user_id: str, new_role: str) -> bool:
    """Update a user's role."""
    if new_role not in ["owner", "admin", "member"]:
        return False

    con = connect()

    con.execute("""
        UPDATE users SET role = ? WHERE id = ?
    """, [new_role, user_id])

    con.close()
    return True


def verify_login(email: str, password: str) -> Optional[Dict]:
    """Verify user login credentials."""
    con = connect()

    password_hash = hashlib.sha256(password.encode()).hexdigest()

    user = con.execute("""
        SELECT * FROM users WHERE email = ? AND password_hash = ?
    """, [email, password_hash]).fetchone()

    if user:
        # Update last login
        con.execute("""
            UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?
        """, [user[0]])

    con.close()

    if not user:
        return None

    return {
        "id": user[0],
        "email": user[1],
        "name": user[2],
        "role": user[4],
        "organization_id": user[5]
    }


def switch_organization(user_id: str, new_org_id: str) -> bool:
    """Switch a user to a different organization."""
    # Check if user is member of the new org
    user = get_user(user_id)
    if not user:
        return False

    # In a real system, you'd check membership in a join table
    # For now, just update if the org exists
    org = get_organization(new_org_id)
    if not org:
        return False

    con = connect()

    con.execute("""
        UPDATE users SET organization_id = ? WHERE id = ?
    """, [new_org_id, user_id])

    con.close()
    return True


def list_organizations() -> List[Dict]:
    """List all organizations."""
    con = connect()

    orgs = con.execute("""
        SELECT * FROM organizations ORDER BY created_at DESC
    """).fetchall()

    con.close()

    return [
        {
            "id": o[0],
            "name": o[1],
            "slug": o[2],
            "created_at": o[3],
            "is_active": o[4]
        }
        for o in orgs
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Multi-tenancy management")
    parser.add_argument("command", choices=["create-org", "create-user", "login", "list-orgs", "list-users", "check-perm"],
                        help="Command to run")
    parser.add_argument("--name", help="Organization or user name")
    parser.add_argument("--slug", help="Organization slug")
    parser.add_argument("--email", help="User email")
    parser.add_argument("--password", help="User password")
    parser.add_argument("--role", default="member", help="User role")
    parser.add_argument("--org", default="default", help="Organization ID")
    parser.add_argument("--perm", help="Permission to check")
    parser.add_argument("--user", help="User ID")

    args = parser.parse_args()

    if args.command == "create-org":
        org_id = create_organization(args.name, args.slug)
        print(f"Organization created: {org_id}")
    elif args.command == "create-user":
        user_id = create_user(args.email, args.name, args.password, args.role, args.org)
        print(f"User created: {user_id}")
    elif args.command == "login":
        user = verify_login(args.email, args.password)
        if user:
            print(f"Login successful: {user['id']} ({user['name']})")
            print(f"Role: {user['role']}, Organization: {user['organization_id']}")
        else:
            print("Login failed")
    elif args.command == "list-orgs":
        orgs = list_organizations()
        print(f"\nOrganizations ({len(orgs)}):")
        for o in orgs:
            print(f"  {o['name']} ({o['id']}) - {o['slug']}")
    elif args.command == "list-users":
        users = get_users_by_organization(args.org)
        print(f"\nUsers in {args.org} ({len(users)}):")
        for u in users:
            print(f"  {u['email']} ({u['name']}) - {u['role']}")
    elif args.command == "check-perm":
        if not args.user or not args.perm:
            print("Error: --user and --perm required")
            sys.exit(1)
        has_perm = check_permission(args.user, args.perm)
        print(f"Permission {args.perm}: {has_perm}")
