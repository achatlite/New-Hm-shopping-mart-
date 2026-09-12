#!/usr/bin/env python3

import os
import time
import getpass

import bcrypt
import mysql.connector
from mysql.connector import Error


DB_CONFIG = {
    "host": os.getenv("DB_HOST", "mysql"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "Sidd"),
    "password": os.getenv("DB_PASSWORD", "Sidd@123"),
    "database": os.getenv("DB_NAME", "hm_shopping_mart"),
}


def connect_with_retry():
    last_error = None

    for attempt in range(1, 31):
        try:
            return mysql.connector.connect(**DB_CONFIG)
        except Error as exc:
            last_error = exc

            if attempt < 30:
                print(f"Waiting for MySQL... {attempt}/30")
                time.sleep(2)

    raise SystemExit(
        f"Could not connect to MySQL after 60 seconds: {last_error}"
    )


def prompt_password():
    show = input("Show password while typing? [y/N]: ").strip().lower() == "y"

    if show:
        password = input("Password: ")
        confirm = input("Confirm Password: ")
    else:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm Password: ")

    if not password:
        raise SystemExit("Password cannot be empty.")

    if password != confirm:
        raise SystemExit("Passwords do not match.")

    return password


def main():
    print("\n=== HM SHOPPING MART - CREATE ADMIN ===\n")

    full_name = input("Admin Name: ").strip()
    email = input("Email: ").strip().lower()
    phone = input("Mobile Number: ").strip()
    store_name = input("Store Name: ").strip()

    if not full_name:
        raise SystemExit("Admin Name is required.")

    if not email:
        raise SystemExit("Email is required.")

    if not phone:
        raise SystemExit("Mobile Number is required.")

    if not store_name:
        raise SystemExit("Store Name is required.")

    password = prompt_password()

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    conn = connect_with_retry()
    cur = conn.cursor(dictionary=True)

    try:
        # ---------------------------------------------------------
        # 1. Get the admin role from the actual roles table.
        # ---------------------------------------------------------
        cur.execute(
            "SELECT id FROM roles WHERE name = %s LIMIT 1",
            ("admin",)
        )

        role = cur.fetchone()

        if not role:
            raise SystemExit(
                "Admin role does not exist in the roles table."
            )

        admin_role_id = role["id"]

        # ---------------------------------------------------------
        # 2. Check whether email or phone already exists.
        # ---------------------------------------------------------
        cur.execute(
            """
            SELECT id, email, phone
            FROM users
            WHERE email = %s OR phone = %s
            LIMIT 1
            """,
            (email, phone),
        )

        existing = cur.fetchone()

        if existing:
            if existing["email"] == email:
                raise SystemExit(
                    "An account with this email already exists."
                )

            if existing["phone"] == phone:
                raise SystemExit(
                    "An account with this mobile number already exists."
                )

            raise SystemExit("An account with these details already exists.")

        # ---------------------------------------------------------
        # 3. Start one transaction.
        #    User + seller profile must be created together.
        # ---------------------------------------------------------

        # ---------------------------------------------------------
        # 4. Create admin user.
        # ---------------------------------------------------------
        cur.execute(
            """
            INSERT INTO users
                (
                    role_id,
                    full_name,
                    email,
                    phone,
                    password_hash,
                    is_active,
                    email_verified
                )
            VALUES
                (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                admin_role_id,
                full_name,
                email,
                phone,
                password_hash,
                1,
                1,
            ),
        )

        admin_user_id = cur.lastrowid

        # ---------------------------------------------------------
        # 5. Create seller profile for this admin.
        #
        # Actual schema:
        # sellers(
        #   id,
        #   user_id,
        #   store_name,
        #   gst_number,
        #   status,
        #   created_at
        # )
        #
        # Product creation requires this seller record.
        # ---------------------------------------------------------
        cur.execute(
            """
            INSERT INTO sellers
                (
                    user_id,
                    store_name,
                    status
                )
            VALUES
                (%s, %s, %s)
            """,
            (
                admin_user_id,
                store_name,
                "approved",
            ),
        )

        seller_id = cur.lastrowid

        # ---------------------------------------------------------
        # 6. Commit both records together.
        # ---------------------------------------------------------
        conn.commit()

        print("\n========================================")
        print("ADMIN CREATED SUCCESSFULLY")
        print("========================================")
        print(f"Name       : {full_name}")
        print(f"Email      : {email}")
        print(f"Mobile     : {phone}")
        print("Role       : admin")
        print(f"User ID    : {admin_user_id}")
        print(f"Seller ID  : {seller_id}")
        print(f"Store Name : {store_name}")
        print("Seller     : approved")
        print("========================================\n")

        print("Admin can now:")
        print("  - Login to Admin Dashboard")
        print("  - Add products")
        print("  - Edit own products")
        print("  - Delete own products")
        print("  - Manage own products only")
        print()

    except Error as exc:
        conn.rollback()
        raise SystemExit(
            f"Admin creation failed. Transaction rolled back: {exc}"
        )

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()

