#!/usr/bin/env python3
import os
import mysql.connector

c = mysql.connector.connect(
    host=os.getenv("DB_HOST","mysql"),
    port=int(os.getenv("DB_PORT","3306")),
    user=os.getenv("DB_USER","Sidd"),
    password=os.getenv("DB_PASSWORD","Sidd@123"),
    database=os.getenv("DB_NAME","hm_shopping_mart"),
)
cur=c.cursor()
cur.execute("DESCRIBE users")
cols=[row[0] for row in cur.fetchall()]
required=["id","role_id","full_name","email","phone","password_hash","is_active","email_verified"]
missing=[x for x in required if x not in cols]
if missing:
    raise SystemExit("Missing users columns: " + ", ".join(missing))
cur.execute("SELECT id FROM roles WHERE name=%s LIMIT 1", ("admin",))
if cur.fetchone() is None:
    raise SystemExit("Missing admin role")
print("Admin schema verification: OK")
cur.close()
c.close()
