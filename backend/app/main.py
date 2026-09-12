import os
import secrets
import zipfile
import threading
import time
import logging
import json

from pathlib import Path
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Response
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

import mysql.connector
import bcrypt


# =========================================================
# LOGGING
# =========================================================

LOG = Path(os.getenv('LOG_DIR', '/app/logs'))
LOG.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger('hm')
logger.setLevel(logging.INFO)
logger.propagate = False

# Keep one live application.log at all times.
# RotatingFileHandler automatically creates application.log.1, .2, etc.
if not logger.handlers:
    h = RotatingFileHandler(
        LOG / 'application.log',
        maxBytes=1024 * 1024,
        backupCount=100,
        encoding='utf-8'
    )
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(h)


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(title='HM Shopping Mart API')

# Prometheus metrics for application/API monitoring.
# Metrics are exposed at /metrics and do not change the existing business APIs.
Instrumentator().instrument(app).expose(app, endpoint='/metrics', include_in_schema=False)


@app.get('/health', include_in_schema=False)
def health():
    return {'status': 'ok', 'service': 'hm-shopping-mart-backend'}


@app.get('/ready', include_in_schema=False)
def ready():
    c = None
    cur = None
    try:
        c = db()
        cur = c.cursor()
        cur.execute('SELECT 1')
        cur.fetchone()
        return {'status': 'ready', 'database': 'ok'}
    except Exception as exc:
        logger.exception('Readiness check failed')
        raise HTTPException(status_code=503, detail='database not ready') from exc
    finally:
        if cur:
            cur.close()
        if c:
            c.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*']
)


# =========================================================
# REQUEST LOGGING
# =========================================================

@app.middleware('http')
async def application_request_logging(request, call_next):
    started = time.time()
    try:
        response = await call_next(request)
        elapsed_ms = (time.time() - started) * 1000
        message = '%s %s status=%s duration_ms=%.1f' % (
            request.method, request.url.path, response.status_code, elapsed_ms
        )
        if response.status_code >= 400:
            logger.error('ERROR %s', message)
        else:
            logger.info('SUCCESS %s', message)
        return response
    except Exception:
        elapsed_ms = (time.time() - started) * 1000
        logger.exception(
            '%s %s status=500 duration_ms=%.1f unhandled_exception',
            request.method, request.url.path, elapsed_ms
        )
        raise


# =========================================================
# DATABASE
# =========================================================

def db():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST', 'mysql'),
        port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER', 'Sidd'),
        password=os.getenv('DB_PASSWORD', 'Sidd@123'),
        database=os.getenv('DB_NAME', 'hm_shopping_mart')
    )


def audit_event(cur, event_type, *, user_id=None, admin_id=None, order_id=None, product_id=None, details=None):
    """Persist a structured business activity without storing payment secrets."""
    cur.execute(
        'INSERT INTO audit_events(event_type,user_id,admin_id,order_id,product_id,details) VALUES(%s,%s,%s,%s,%s,%s)',
        (event_type, user_id, admin_id, order_id, product_id, json.dumps(details or {}, ensure_ascii=False, default=str))
    )


def ensure_audit_table():
    c=db(); cur=c.cursor()
    try:
        cur.execute('''CREATE TABLE IF NOT EXISTS audit_events (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(80) NOT NULL,
            user_id BIGINT UNSIGNED NULL,
            admin_id BIGINT UNSIGNED NULL,
            order_id BIGINT UNSIGNED NULL,
            product_id BIGINT UNSIGNED NULL,
            details JSON NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_audit_order (order_id, id),
            INDEX idx_audit_product (product_id, id),
            INDEX idx_audit_created (created_at),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(admin_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE SET NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        )''')
        try:
            cur.execute('ALTER TABLE return_requests ADD COLUMN admin_id BIGINT UNSIGNED NULL AFTER user_id')
        except mysql.connector.Error as e:
            if getattr(e, 'errno', None) != 1060:
                raise
        for ddl in [
            'ALTER TABLE addresses ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP',
            'ALTER TABLE shipments ADD COLUMN current_city VARCHAR(100) NULL AFTER tracking_number',
            'ALTER TABLE shipments ADD COLUMN current_location VARCHAR(255) NULL AFTER current_city',
            'ALTER TABLE shipments ADD COLUMN estimated_delivery DATETIME NULL AFTER current_location'
        ]:
            try:
                cur.execute(ddl)
            except mysql.connector.Error as e:
                if getattr(e, 'errno', None) != 1060:
                    raise
        c.commit()

        # Product deletion must not destroy historical orders. Make the order-item
        # product reference nullable and SET NULL on product deletion.
        try:
            cur.execute("ALTER TABLE order_items MODIFY COLUMN product_id BIGINT UNSIGNED NULL")
        except mysql.connector.Error:
            pass
        try:
            cur.execute("SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='order_items' AND COLUMN_NAME='product_id' AND REFERENCED_TABLE_NAME='products' LIMIT 1")
            fk = cur.fetchone()
            fk_name = fk[0] if fk and not isinstance(fk, dict) else (fk or {}).get('CONSTRAINT_NAME')
            if fk_name:
                cur.execute(f"ALTER TABLE order_items DROP FOREIGN KEY `{fk_name}`")
                cur.execute("ALTER TABLE order_items ADD CONSTRAINT fk_order_items_product FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL")
        except mysql.connector.Error:
            pass
        try:
            cur.execute('''CREATE TABLE IF NOT EXISTS shipment_tracking_events (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                shipment_id BIGINT UNSIGNED NOT NULL,
                status VARCHAR(40) NOT NULL,
                city VARCHAR(100),
                location VARCHAR(255),
                note VARCHAR(500),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_shipment_events (shipment_id,id),
                FOREIGN KEY(shipment_id) REFERENCES shipments(id) ON DELETE CASCADE
            )''')
        except mysql.connector.Error:
            pass
    finally:
        cur.close(); c.close()


# =========================================================
# LOG ARCHIVE JOB
# =========================================================

def _archive_previous_day_logs():
    """Create a daily ZIP snapshot without deleting the active log."""
    now = datetime.utcnow()
    yesterday = (now - timedelta(days=1)).date()
    archive = LOG / f'logs-{yesterday}.zip'

    # Archive rotated logs that were last modified on/before yesterday.
    candidates = []
    for p in LOG.glob('application.log.*'):
        if not p.is_file():
            continue
        try:
            day = datetime.utcfromtimestamp(p.stat().st_mtime).date()
        except OSError:
            continue
        if day <= yesterday:
            candidates.append(p)

    # If no rotated file exists, snapshot the live log. Never unlink it.
    active = LOG / 'application.log'
    if active.is_file() and not archive.exists():
        candidates.append(active)

    if candidates:
        mode = 'a' if archive.exists() else 'w'
        with zipfile.ZipFile(archive, mode, zipfile.ZIP_DEFLATED) as z:
            existing = set(z.namelist())
            for p in candidates:
                arcname = p.name if p.name != 'application.log' else f'application-{yesterday}.log'
                if arcname not in existing:
                    z.write(p, arcname)


def archive_job():
    # Run hourly. The active application.log is NEVER deleted by this job.
    while True:
        try:
            _archive_previous_day_logs()

            cutoff = datetime.utcnow() - timedelta(days=5)
            for p in LOG.glob('logs-*.zip'):
                try:
                    if datetime.utcfromtimestamp(p.stat().st_mtime) < cutoff:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass

            # Rotated logs older than 5 days can also be removed safely.
            for p in LOG.glob('application.log.*'):
                try:
                    if datetime.utcfromtimestamp(p.stat().st_mtime) < cutoff:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass

        except Exception:
            logger.exception('Daily log archive failed')

        time.sleep(3600)


threading.Thread(target=archive_job, daemon=True).start()
try:
    ensure_audit_table()
    logger.info('Audit activity table ready')
except Exception:
    logger.exception('Audit activity table initialization failed')
logger.info('HM Shopping Mart backend logging initialized')


# =========================================================
# REQUEST MODELS
# =========================================================

class Credentials(BaseModel):

    identifier: str
    password: str


class Register(BaseModel):

    full_name: str
    username: str
    email: EmailStr
    password: str
    phone: str | None = None


class SupportTicketIn(BaseModel):
    subject: str
    message: str


class PasswordReset(BaseModel):

    identifier: str
    new_password: str


class AdminCreate(BaseModel):

    full_name: str
    username: str
    email: EmailStr
    phone: str
    password: str


class ProductIn(BaseModel):

    name: str
    sku: str
    category_id: int
    brand_id: int | None = None
    description: str | None = None
    price: float
    mrp: float
    discount_percent: float = 0
    stock_quantity: int = 0
    tax_percent: float = 0
    status: str = 'active'
    image_url: str | None = None


class AddressIn(BaseModel):

    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = 'India'
    address_type: str = 'home'
    is_default: bool = False


class BuyIn(BaseModel):

    product_id: int
    quantity: int = 1
    address: AddressIn
    address_id: int | None = None
    payment_method: str = 'demo'


class CartItemIn(BaseModel):

    product_id: int
    quantity: int = 1


class CartCheckoutIn(BaseModel):

    address: AddressIn
    address_id: int | None = None
    payment_method: str = 'demo'


# =========================================================
# RETURN REQUEST MODEL
# =========================================================

class ReturnRequestIn(BaseModel):

    order_id: int
    order_item_id: int
    quantity: int = 1
    reason: str


# =========================================================
# ADMIN RETURN PROCESS MODEL
# =========================================================

class ReturnProcessIn(BaseModel):

    status: str
    admin_note: str | None = None
    refund_amount: float | None = None


# =========================================================
# SHIPMENT MODEL
# =========================================================

class ShipmentIn(BaseModel):

    carrier: str | None = None
    tracking_number: str | None = None
    status: str = 'pending'
    current_city: str | None = None
    current_location: str | None = None
    estimated_delivery: str | None = None


# =========================================================
# HEALTH
# =========================================================

@app.get('/api/health')
def health():

    return {
        'status': 'ok'
    }


# =========================================================
# USER HELPERS
# =========================================================

def user_by_id(uid):

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT u.*,r.name role_name "
            "FROM users u "
            "JOIN roles r ON r.id=u.role_id "
            "WHERE u.id=%s "
            "AND u.is_active=TRUE",
            (uid,)
        )

        return cur.fetchone()

    finally:

        cur.close()
        c.close()


def auth(x, role):

    identifier = x.identifier.strip()

    if not identifier:

        raise HTTPException(
            400,
            'Email, mobile number or username is required'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT u.*,r.name role_name "
            "FROM users u "
            "JOIN roles r ON r.id=u.role_id "
            "WHERE ("
            "LOWER(u.email)=LOWER(%s) "
            "OR u.phone=%s "
            "OR LOWER(u.username)=LOWER(%s)"
            ") "
            "AND u.is_active=TRUE "
            "LIMIT 1",
            (
                identifier,
                identifier,
                identifier
            )
        )

        u = cur.fetchone()

        if (
            not u
            or u['role_name'] != role
            or not bcrypt.checkpw(
                x.password.encode(),
                u['password_hash'].encode()
            )
        ):

            raise HTTPException(
                401,
                'Invalid credentials'
            )

        cur.execute(
            'UPDATE users '
            'SET last_login_at=UTC_TIMESTAMP() '
            'WHERE id=%s',
            (u['id'],)
        )

        c.commit()

        logger.info(
            '%s login user_id=%s identifier=%s',
            role,
            u['id'],
            identifier
        )

        return {
            'id': u['id'],
            'name': u['full_name'],
            'username': u.get('username'),
            'email': u['email'],
            'phone': u.get('phone'),
            'role': role
        }

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER REGISTRATION
# =========================================================

@app.post('/api/auth/register')
def register(x: Register):

    full_name = x.full_name.strip()
    username = x.username.strip().lower()
    email = str(x.email).strip().lower()
    phone = (
        x.phone.strip()
        if x.phone
        else None
    )

    if not full_name:

        raise HTTPException(
            400,
            'Full name is required.'
        )

    if not username:

        raise HTTPException(
            400,
            'Username is required.'
        )

    if (
        len(username) < 3
        or len(username) > 80
    ):

        raise HTTPException(
            400,
            'Username must be between 3 and 80 characters.'
        )

    if not all(
        ch.isalnum() or ch in '._'
        for ch in username
    ):

        raise HTTPException(
            400,
            'Username can contain only letters, numbers, underscore or dot.'
        )

    if len(x.password) < 6:

        raise HTTPException(
            400,
            'Password must be at least 6 characters.'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT id FROM roles "
            "WHERE name='customer' "
            "LIMIT 1"
        )

        role_row = cur.fetchone()

        if not role_row:

            raise HTTPException(
                500,
                'Customer role not found'
            )

        role = role_row['id']


        cur.execute(
            "SELECT id FROM users "
            "WHERE LOWER(username)=LOWER(%s) "
            "LIMIT 1",
            (username,)
        )

        if cur.fetchone():

            raise HTTPException(
                409,
                'Username already exists. Please choose another username.'
            )


        cur.execute(
            "SELECT id FROM users "
            "WHERE LOWER(email)=LOWER(%s) "
            "LIMIT 1",
            (email,)
        )

        if cur.fetchone():

            raise HTTPException(
                409,
                'Email already exists.'
            )


        if phone:

            cur.execute(
                "SELECT id FROM users "
                "WHERE phone=%s "
                "LIMIT 1",
                (phone,)
            )

            if cur.fetchone():

                raise HTTPException(
                    409,
                    'Mobile number already exists.'
                )


        password_hash = bcrypt.hashpw(
            x.password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')


        cur.execute(
            'INSERT INTO users('
            'role_id,'
            'full_name,'
            'username,'
            'email,'
            'password_hash,'
            'phone,'
            'email_verified,'
            'is_active'
            ') VALUES(%s,%s,%s,%s,%s,%s,TRUE,TRUE)',
            (
                role,
                full_name,
                username,
                email,
                password_hash,
                phone
            )
        )

        uid = cur.lastrowid

        c.commit()

        logger.info(
            'customer registered user_id=%s username=%s',
            uid,
            username
        )

        return {
            'message': 'Registration successful',
            'user': {
                'id': uid,
                'name': full_name,
                'username': username,
                'email': email,
                'phone': phone,
                'role': 'customer'
            }
        }

    except mysql.connector.IntegrityError:

        c.rollback()

        raise HTTPException(
            409,
            'Username, email or mobile number already exists.'
        )

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Customer registration failed'
        )

        raise HTTPException(
            500,
            'Unable to create customer account.'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# LOGIN
# =========================================================

@app.post('/api/auth/login')
def login(x: Credentials):

    return auth(
        x,
        'customer'
    )


@app.post('/api/admin/login')
def admin_login(x: Credentials):

    return auth(
        x,
        'admin'
    )


# =========================================================
# ADMIN RESET PASSWORD
# =========================================================

@app.post('/api/admin/reset-password')
def admin_reset_password(
    x: PasswordReset
):

    identifier = x.identifier.strip()
    new_password = x.new_password

    if not identifier:

        raise HTTPException(
            400,
            'Admin email, mobile number or username is required.'
        )

    if not new_password:

        raise HTTPException(
            400,
            'New password is required.'
        )

    if len(new_password) < 6:

        raise HTTPException(
            400,
            'Password must be at least 6 characters.'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT "
            "u.id,"
            "u.username,"
            "u.email,"
            "u.phone,"
            "r.name AS role_name "
            "FROM users u "
            "JOIN roles r ON r.id=u.role_id "
            "WHERE ("
            "LOWER(u.email)=LOWER(%s) "
            "OR u.phone=%s "
            "OR LOWER(u.username)=LOWER(%s)"
            ") "
            "AND u.is_active=TRUE "
            "AND r.name='admin' "
            "LIMIT 1",
            (
                identifier,
                identifier,
                identifier
            )
        )

        user = cur.fetchone()

        if not user:

            raise HTTPException(
                401,
                'Admin email, mobile number or username is incorrect.'
            )

        password_hash = bcrypt.hashpw(
            new_password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        cur.execute(
            "UPDATE users "
            "SET password_hash=%s, "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE id=%s",
            (
                password_hash,
                user['id']
            )
        )

        c.commit()

        logger.info(
            'Admin password reset successfully user_id=%s identifier=%s',
            user['id'],
            identifier
        )

        return {
            'message':
            'Admin password reset successfully.'
        }

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Admin password reset failed'
        )

        raise HTTPException(
            500,
            'Unable to reset admin password.'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER RESET PASSWORD
# =========================================================

@app.post('/api/auth/reset-password')
def customer_reset_password(
    x: PasswordReset
):

    identifier = x.identifier.strip()
    new_password = x.new_password

    if not identifier:

        raise HTTPException(
            400,
            'Email or mobile number is required.'
        )

    if not new_password:

        raise HTTPException(
            400,
            'New password is required.'
        )

    if len(new_password) < 6:

        raise HTTPException(
            400,
            'Password must be at least 6 characters.'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT "
            "u.id,"
            "u.email,"
            "u.phone,"
            "r.name AS role_name "
            "FROM users u "
            "JOIN roles r ON r.id=u.role_id "
            "WHERE ("
            "LOWER(u.email)=LOWER(%s) "
            "OR u.phone=%s"
            ") "
            "AND u.is_active=TRUE "
            "AND r.name='customer' "
            "LIMIT 1",
            (
                identifier,
                identifier
            )
        )

        user = cur.fetchone()

        if not user:

            raise HTTPException(
                401,
                'Email or mobile number is incorrect.'
            )

        password_hash = bcrypt.hashpw(
            new_password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        cur.execute(
            "UPDATE users "
            "SET password_hash=%s, "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE id=%s",
            (
                password_hash,
                user['id']
            )
        )

        c.commit()

        logger.info(
            'Customer password reset successfully user_id=%s identifier=%s',
            user['id'],
            identifier
        )

        return {
            'message':
            'Password reset successful.'
        }

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Customer password reset failed'
        )

        raise HTTPException(
            500,
            'Unable to reset password.'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# CREATE NEW ADMIN ACCOUNT
# =========================================================

@app.post('/api/admin/create')
def create_admin(
    x: AdminCreate
):

    full_name = x.full_name.strip()
    username = x.username.strip().lower()
    new_email = str(x.email).strip().lower()
    new_phone = x.phone.strip()
    new_password = x.password

    if not full_name:

        raise HTTPException(
            400,
            'Admin full name is required.'
        )

    if not username:

        raise HTTPException(
            400,
            'Admin username is required.'
        )

    if len(username) < 3:

        raise HTTPException(
            400,
            'Admin username must be at least 3 characters.'
        )

    if not new_email:

        raise HTTPException(
            400,
            'Admin email is required.'
        )

    if not new_phone:

        raise HTTPException(
            400,
            'Admin mobile number is required.'
        )

    if not new_password:

        raise HTTPException(
            400,
            'Admin password is required.'
        )

    if len(new_password) < 6:

        raise HTTPException(
            400,
            'Admin password must be at least 6 characters.'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT id FROM users "
            "WHERE LOWER(username)=LOWER(%s) "
            "LIMIT 1",
            (username,)
        )

        if cur.fetchone():

            raise HTTPException(
                409,
                'Admin username already exists.'
            )


        cur.execute(
            "SELECT id FROM users "
            "WHERE LOWER(email)=LOWER(%s) "
            "LIMIT 1",
            (new_email,)
        )

        if cur.fetchone():

            raise HTTPException(
                409,
                'Admin email already exists.'
            )


        cur.execute(
            "SELECT id FROM users "
            "WHERE phone=%s "
            "LIMIT 1",
            (new_phone,)
        )

        if cur.fetchone():

            raise HTTPException(
                409,
                'Admin mobile number already exists.'
            )


        cur.execute(
            "SELECT id FROM roles "
            "WHERE name='admin' "
            "LIMIT 1"
        )

        admin_role = cur.fetchone()

        if not admin_role:

            raise HTTPException(
                500,
                'Admin role not found.'
            )

        admin_role_id = admin_role['id']


        password_hash = bcrypt.hashpw(
            new_password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')


        cur.execute(
            "INSERT INTO users("
            "role_id,"
            "full_name,"
            "username,"
            "email,"
            "password_hash,"
            "phone,"
            "email_verified,"
            "is_active"
            ") VALUES(%s,%s,%s,%s,%s,%s,TRUE,TRUE)",
            (
                admin_role_id,
                full_name,
                username,
                new_email,
                password_hash,
                new_phone
            )
        )

        new_admin_id = cur.lastrowid


        cur.execute(
            "INSERT INTO sellers("
            "user_id,"
            "store_name,"
            "status"
            ") VALUES(%s,%s,'approved')",
            (
                new_admin_id,
                full_name + " Store"
            )
        )

        new_seller_id = cur.lastrowid

        c.commit()

        logger.info(
            'New admin created user_id=%s username=%s email=%s',
            new_admin_id,
            username,
            new_email
        )

        return {
            'message':
            'Admin account created successfully.',
            'admin': {
                'id': new_admin_id,
                'name': full_name,
                'username': username,
                'email': new_email,
                'phone': new_phone,
                'role': 'admin',
                'seller_id': new_seller_id,
                'store_name':
                full_name + ' Store',
                'seller_status':
                'approved'
            }
        }

    except mysql.connector.IntegrityError:

        c.rollback()

        raise HTTPException(
            409,
            'Username, email or mobile number already exists.'
        )

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Admin account creation failed'
        )

        raise HTTPException(
            500,
            'Unable to create admin account.'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# IMAGE PROXY
# =========================================================

@app.get('/api/image-proxy')
def image_proxy(
    url: str
):

    from urllib.parse import urlparse
    from urllib.request import Request, urlopen
    import ipaddress
    import socket

    parsed = urlparse(
        url.strip()
    )

    if (
        parsed.scheme not in (
            'http',
            'https'
        )
        or not parsed.hostname
    ):

        raise HTTPException(
            400,
            'Only http:// or https:// image URLs are allowed'
        )

    if (
        parsed.port
        and parsed.port not in (
            80,
            443
        )
    ):

        raise HTTPException(
            400,
            'Only standard HTTP/HTTPS ports are allowed'
        )

    host = (
        parsed.hostname
        .lower()
        .rstrip('.')
    )

    blocked_names = {
        'localhost',
        'localhost.localdomain',
        '0.0.0.0',
        '127.0.0.1',
        '::1'
    }

    if (
        host in blocked_names
        or host.endswith('.local')
        or host.endswith('.internal')
    ):

        raise HTTPException(
            400,
            'Private/local image addresses are not allowed'
        )

    try:

        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                None
            )
        }

        for address in addresses:

            ip = ipaddress.ip_address(
                address
            )

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):

                raise HTTPException(
                    400,
                    'Private/local image addresses are not allowed'
                )

    except socket.gaierror:

        raise HTTPException(
            400,
            'Image host could not be resolved'
        )

    try:

        req = Request(
            url.strip(),
            headers={
                'User-Agent':
                'Mozilla/5.0 HM-Shopping-Mart-Image-Preview'
            }
        )

        with urlopen(
            req,
            timeout=10
        ) as remote:

            content_type = (
                remote.headers.get(
                    'Content-Type'
                )
                or ''
            ).split(
                ';',
                1
            )[0].strip().lower()

            if not content_type.startswith(
                'image/'
            ):

                raise HTTPException(
                    400,
                    'URL does not point directly to an image. '
                    'Use Copy image address.'
                )

            data = remote.read(
                8 * 1024 * 1024 + 1
            )

            if len(data) > 8 * 1024 * 1024:

                raise HTTPException(
                    413,
                    'Image is too large. Maximum size is 8 MB.'
                )

            return Response(
                content=data,
                media_type=content_type,
                headers={
                    'Cache-Control':
                    'public, max-age=3600'
                }
            )

    except HTTPException:

        raise

    except Exception:

        raise HTTPException(
            400,
            'Image URL could not be loaded. '
            'Use the direct image address '
            '(Copy image address).'
        )


# =========================================================
# CATEGORIES
# =========================================================

@app.get('/api/categories')
def categories():

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT id,name FROM categories '
            'WHERE is_active=TRUE '
            'ORDER BY name'
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# PRODUCTS
# =========================================================

@app.get('/api/products')
def products():

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT p.*,c.name category,b.name brand,"
            "COALESCE(p.discount_percent,"
            "CASE WHEN p.mrp>0 "
            "THEN ROUND((p.mrp-p.price)*100/p.mrp,2) "
            "ELSE 0 END) discount_percent,"
            "COALESCE(("
            "SELECT image_url FROM product_images pi "
            "WHERE pi.product_id=p.id "
            "AND pi.is_primary=TRUE "
            "LIMIT 1"
            "),'') image_url "
            "FROM products p "
            "JOIN categories c ON c.id=p.category_id "
            "LEFT JOIN brands b ON b.id=p.brand_id "
            "WHERE p.status='active' "
            "ORDER BY p.id DESC"
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


@app.get('/api/products/bestsellers')
def best_sellers():

    c = db()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT p.*, c.name category, b.name brand, "
            "COALESCE(p.discount_percent, CASE WHEN p.mrp>0 THEN ROUND((p.mrp-p.price)*100/p.mrp,2) ELSE 0 END) discount_percent, "
            "COALESCE((SELECT image_url FROM product_images pi WHERE pi.product_id=p.id AND pi.is_primary=TRUE LIMIT 1),'') image_url, "
            "COALESCE(SUM(CASE WHEN o.status NOT IN ('cancelled','returned') THEN oi.quantity ELSE 0 END),0) sold_quantity "
            "FROM products p JOIN categories c ON c.id=p.category_id LEFT JOIN brands b ON b.id=p.brand_id "
            "LEFT JOIN order_items oi ON oi.product_id=p.id LEFT JOIN orders o ON o.id=oi.order_id "
            "WHERE p.status='active' GROUP BY p.id ORDER BY sold_quantity DESC, p.id DESC LIMIT 12"
        )
        return cur.fetchall()
    finally:
        cur.close(); c.close()


@app.get('/api/products/{pid}')
def product(
    pid: int
):

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT p.*,c.name category,b.name brand,"
            "COALESCE(p.discount_percent,"
            "CASE WHEN p.mrp>0 "
            "THEN ROUND((p.mrp-p.price)*100/p.mrp,2) "
            "ELSE 0 END) discount_percent,"
            "COALESCE(("
            "SELECT image_url FROM product_images pi "
            "WHERE pi.product_id=p.id "
            "AND pi.is_primary=TRUE "
            "LIMIT 1"
            "),'') image_url "
            "FROM products p "
            "JOIN categories c ON c.id=p.category_id "
            "LEFT JOIN brands b ON b.id=p.brand_id "
            "WHERE p.id=%s",
            (pid,)
        )

        r = cur.fetchone()

    finally:

        cur.close()
        c.close()

    if not r:

        raise HTTPException(
            404,
            'Product not found'
        )

    return r


# =========================================================
# CUSTOMER SUPPORT
# =========================================================

@app.get('/api/support/{uid}')
def support_tickets(uid: int):

    require_customer(uid)
    c = db()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, subject, message, status, created_at, updated_at "
            "FROM support_tickets WHERE user_id=%s ORDER BY id DESC",
            (uid,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        c.close()


@app.post('/api/support/{uid}')
def create_support_ticket(uid: int, x: SupportTicketIn):

    require_customer(uid)
    subject = x.subject.strip()
    message = x.message.strip()
    if not subject:
        raise HTTPException(400, 'Subject is required.')
    if not message:
        raise HTTPException(400, 'Message is required.')
    if len(subject) > 255:
        raise HTTPException(400, 'Subject is too long.')

    c = db()
    cur = c.cursor(dictionary=True)
    try:
        cur.execute(
            "INSERT INTO support_tickets(user_id,subject,message) VALUES(%s,%s,%s)",
            (uid, subject, message)
        )
        c.commit()
        return {'message': 'Support ticket created successfully.', 'id': cur.lastrowid}
    except Exception:
        c.rollback()
        logger.exception('Support ticket creation failed user_id=%s', uid)
        raise HTTPException(500, 'Unable to create support ticket.')
    finally:
        cur.close()
        c.close()


# =========================================================
# ADMIN ACCESS
# =========================================================

def require_admin(uid):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'admin'
    ):

        raise HTTPException(
            403,
            'Admin access required'
        )

    return u


def own_seller(uid):

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT id FROM sellers '
            'WHERE user_id=%s',
            (uid,)
        )

        s = cur.fetchone()

    finally:

        cur.close()
        c.close()

    if not s:

        raise HTTPException(
            403,
            'Admin seller profile not found'
        )

    return s['id']


# =========================================================
# ADMIN PRODUCTS
# =========================================================

@app.get('/api/admin/products/{uid}')
def admin_products(
    uid: int
):

    require_admin(uid)
    sid = own_seller(uid)

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            "SELECT p.*,c.name category,"
            "COALESCE(("
            "SELECT image_url "
            "FROM product_images pi "
            "WHERE pi.product_id=p.id "
            "AND pi.is_primary=TRUE "
            "LIMIT 1"
            "),'') image_url "
            "FROM products p "
            "JOIN categories c ON c.id=p.category_id "
            "WHERE p.seller_id=%s "
            "ORDER BY p.id DESC",
            (sid,)
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# ADD PRODUCT
# =========================================================

@app.post('/api/admin/products/{uid}')
def add_product(uid: int, x: ProductIn):
    require_admin(uid)
    sid = own_seller(uid)

    if x.mrp <= 0 or x.price < 0 or x.price > x.mrp:
        raise HTTPException(400, 'Selling price must be between 0 and MRP')
    if x.discount_percent < 0 or x.discount_percent > 100:
        raise HTTPException(400, 'Discount must be between 0 and 100%')

    c = db()
    cur = c.cursor()
    try:
        slug = x.name.lower().strip().replace(' ', '-') + '-' + x.sku.lower().strip()
        cur.execute(
            'INSERT INTO products('
            'seller_id,category_id,brand_id,name,slug,sku,description,'
            'price,mrp,discount_percent,stock_quantity,tax_percent,status'
            ') VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (sid, x.category_id, x.brand_id, x.name, slug, x.sku, x.description,
             x.price, x.mrp, x.discount_percent, x.stock_quantity, x.tax_percent, x.status)
        )
        pid = cur.lastrowid

        if x.image_url and x.image_url.strip():
            cur.execute(
                'INSERT INTO product_images(product_id,image_url,sort_order,is_primary) '
                'VALUES(%s,%s,0,TRUE)', (pid, x.image_url.strip())
            )

        audit_event(cur, 'PRODUCT_CREATED', admin_id=uid, product_id=pid,
                    details={'product_name': x.name, 'sku': x.sku, 'image_url': x.image_url})
        c.commit()
        logger.info('admin user_id=%s created product_id=%s image=%s', uid, pid, bool(x.image_url))
        return {'message': 'Product added', 'id': pid}
    except mysql.connector.IntegrityError:
        c.rollback()
        raise HTTPException(409, 'SKU or product slug already exists')
    except Exception:
        c.rollback()
        logger.exception('Failed to create product for admin user_id=%s', uid)
        raise
    finally:
        cur.close()
        c.close()


@app.put('/api/admin/products/{uid}/{pid}')
def edit_product(uid: int, pid: int, x: ProductIn):
    require_admin(uid)
    sid = own_seller(uid)

    if x.mrp <= 0 or x.price < 0 or x.price > x.mrp:
        raise HTTPException(400, 'Selling price must be between 0 and MRP')
    if x.discount_percent < 0 or x.discount_percent > 100:
        raise HTTPException(400, 'Discount must be between 0 and 100%')

    c = db()
    cur = c.cursor()
    try:
        cur.execute(
            'SELECT id FROM products '
            'WHERE id=%s AND seller_id=%s '
            'LIMIT 1',
            (pid, sid)
        )

        product = cur.fetchone()

        if not product:
            c.rollback()
            raise HTTPException(
                403,
                'You can modify only your own products'
            )

        cur.execute(
            'UPDATE products SET category_id=%s,brand_id=%s,name=%s,description=%s,'
            'price=%s,mrp=%s,discount_percent=%s,stock_quantity=%s,tax_percent=%s,status=%s '
            'WHERE id=%s AND seller_id=%s',
            (x.category_id, x.brand_id, x.name, x.description, x.price, x.mrp,
             x.discount_percent, x.stock_quantity, x.tax_percent, x.status, pid, sid)
        )

        cur.execute('SELECT id FROM product_images WHERE product_id=%s AND is_primary=TRUE LIMIT 1', (pid,))
        img = cur.fetchone()
        if x.image_url and x.image_url.strip():
            if img:
                cur.execute('UPDATE product_images SET image_url=%s WHERE id=%s', (x.image_url.strip(), img[0]))
            else:
                cur.execute(
                    'INSERT INTO product_images(product_id,image_url,sort_order,is_primary) VALUES(%s,%s,0,TRUE)',
                    (pid, x.image_url.strip())
                )
        else:
            cur.execute('DELETE FROM product_images WHERE product_id=%s', (pid,))

        audit_event(cur, 'PRODUCT_UPDATED', admin_id=uid, product_id=pid,
                    details={'product_name': x.name, 'sku': x.sku, 'image_url': x.image_url})
        c.commit()
        logger.info('admin user_id=%s edited own product_id=%s image=%s', uid, pid, bool(x.image_url))
        return {'message': 'Product updated'}
    except HTTPException:
        raise
    except Exception:
        c.rollback()
        logger.exception('Failed to update product_id=%s for admin user_id=%s', pid, uid)
        raise
    finally:
        cur.close()
        c.close()


# =========================================================
# DELETE PRODUCT
# =========================================================

@app.delete('/api/admin/products/{uid}/{pid}')
def delete_product(
    uid: int,
    pid: int
):

    require_admin(uid)
    sid = own_seller(uid)

    c = db()
    cur = c.cursor()

    try:

        cur.execute('SELECT name,sku FROM products WHERE id=%s AND seller_id=%s LIMIT 1',(pid,sid))
        product_info = cur.fetchone() or {}
        cur.execute(
            'DELETE FROM products '
            'WHERE id=%s AND seller_id=%s',
            (
                pid,
                sid
            )
        )

        if cur.rowcount == 0:

            c.rollback()

            raise HTTPException(
                403,
                'You can delete only your own products'
            )

        audit_event(cur, 'PRODUCT_DELETED', admin_id=uid, product_id=pid, details={
            'product_name': product_info.get('name'), 'sku': product_info.get('sku'), 'admin_id': uid
        })
        c.commit()

        logger.info(
            'admin user_id=%s deleted own product_id=%s',
            uid,
            pid
        )

        return {
            'message': 'Product deleted'
        }

    finally:

        cur.close()
        c.close()


# =========================================================
# =========================================================
# CUSTOMER ADDRESSES
# =========================================================

@app.get('/api/addresses/{uid}')
def customer_addresses(uid: int):
    require_customer(uid)
    c=db(); cur=c.cursor(dictionary=True)
    try:
        cur.execute('SELECT id,user_id,address_type,recipient_name,phone,address_line1,address_line2,city,state,postal_code,country,is_default,created_at,updated_at FROM addresses WHERE user_id=%s ORDER BY is_default DESC,id DESC',(uid,))
        return cur.fetchall()
    finally:
        cur.close(); c.close()


@app.put('/api/addresses/{uid}/{address_id}')
def update_customer_address(uid: int, address_id: int, x: AddressIn):
    require_customer(uid)
    c=db(); cur=c.cursor()
    try:
        cur.execute('SELECT id FROM addresses WHERE id=%s AND user_id=%s LIMIT 1 FOR UPDATE',(address_id,uid))
        if not cur.fetchone():
            raise HTTPException(404,'Address not found')
        cur.execute('UPDATE addresses SET address_type=%s,recipient_name=%s,phone=%s,address_line1=%s,address_line2=%s,city=%s,state=%s,postal_code=%s,country=%s,is_default=TRUE WHERE id=%s AND user_id=%s',(x.address_type,x.recipient_name,x.phone,x.address_line1,x.address_line2,x.city,x.state,x.postal_code,x.country,address_id,uid))
        cur.execute('UPDATE addresses SET is_default=FALSE WHERE user_id=%s AND id<>%s',(uid,address_id))
        c.commit(); logger.info('ADDRESS_UPDATED customer_id=%s address_id=%s city=%s state=%s',uid,address_id,x.city,x.state)
        return {'message':'Address updated successfully','address_id':address_id}
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); logger.exception('Address update failed customer_id=%s address_id=%s',uid,address_id); raise HTTPException(500,'Unable to update address')
    finally:
        cur.close(); c.close()


# CUSTOMER CART
# =========================================================

def require_customer(uid):

    u = user_by_id(uid)

    if not u or u['role_name'] != 'customer':
        raise HTTPException(403, 'Only normal customers can use the cart')

    return u


def get_or_create_cart(cur, uid):

    cur.execute('SELECT id FROM carts WHERE user_id=%s FOR UPDATE', (uid,))
    row = cur.fetchone()

    if row:
        return row['id']

    cur.execute('INSERT INTO carts(user_id) VALUES(%s)', (uid,))
    return cur.lastrowid


@app.get('/api/cart/{uid}')
def get_cart(uid: int):

    require_customer(uid)
    c = db()
    cur = c.cursor(dictionary=True)

    try:
        cur.execute(
            'SELECT ci.id,ci.product_id,ci.quantity,ci.unit_price,'
            'p.name,p.mrp,p.discount_percent,p.stock_quantity,p.status,'
            'COALESCE((SELECT pi.image_url FROM product_images pi '
            'WHERE pi.product_id=p.id AND pi.is_primary=TRUE LIMIT 1),\'\') AS image_url '
            'FROM carts ca JOIN cart_items ci ON ci.cart_id=ca.id '
            'JOIN products p ON p.id=ci.product_id '
            'WHERE ca.user_id=%s ORDER BY ci.id',
            (uid,)
        )
        return cur.fetchall()
    finally:
        cur.close(); c.close()


@app.post('/api/cart/{uid}')
def add_to_cart(uid: int, x: CartItemIn):

    require_customer(uid)
    if x.quantity < 1:
        raise HTTPException(400, 'Quantity must be at least 1')

    c = db(); cur = c.cursor(dictionary=True)
    try:
        cur.execute('SELECT id,price,stock_quantity,status FROM products WHERE id=%s FOR UPDATE', (x.product_id,))
        p = cur.fetchone()
        if not p or p['status'] != 'active':
            raise HTTPException(404, 'Product not found or unavailable')
        if p['stock_quantity'] < x.quantity:
            raise HTTPException(400, 'Insufficient stock')

        cart_id = get_or_create_cart(cur, uid)
        cur.execute('SELECT id,quantity FROM cart_items WHERE cart_id=%s AND product_id=%s LIMIT 1 FOR UPDATE', (cart_id,x.product_id))
        item = cur.fetchone()
        new_qty = x.quantity + int(item['quantity']) if item else x.quantity
        if new_qty > p['stock_quantity']:
            raise HTTPException(400, 'Requested quantity exceeds available stock')

        if item:
            cur.execute('UPDATE cart_items SET quantity=%s,unit_price=%s WHERE id=%s', (new_qty,p['price'],item['id']))
        else:
            cur.execute('INSERT INTO cart_items(cart_id,product_id,quantity,unit_price) VALUES(%s,%s,%s,%s)', (cart_id,x.product_id,new_qty,p['price']))
        c.commit()
        return {'message':'Product added to cart','quantity':new_qty}
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); logger.exception('Add to cart failed')
        raise HTTPException(500,'Unable to update cart')
    finally:
        cur.close(); c.close()


@app.put('/api/cart/{uid}/{product_id}')
def update_cart_item(uid: int, product_id: int, x: CartItemIn):

    require_customer(uid)
    if x.quantity < 1:
        raise HTTPException(400, 'Quantity must be at least 1')

    c=db(); cur=c.cursor(dictionary=True)
    try:
        cur.execute('SELECT id,price,stock_quantity,status FROM products WHERE id=%s FOR UPDATE',(product_id,))
        p=cur.fetchone()
        if not p or p['status'] != 'active': raise HTTPException(404,'Product not found or unavailable')
        if x.quantity > p['stock_quantity']: raise HTTPException(400,'Insufficient stock')
        cur.execute('SELECT ca.id cart_id,ci.id FROM carts ca JOIN cart_items ci ON ci.cart_id=ca.id WHERE ca.user_id=%s AND ci.product_id=%s FOR UPDATE',(uid,product_id))
        item=cur.fetchone()
        if not item: raise HTTPException(404,'Cart item not found')
        cur.execute('UPDATE cart_items SET quantity=%s,unit_price=%s WHERE id=%s',(x.quantity,p['price'],item['id']))
        c.commit(); return {'message':'Cart updated','quantity':x.quantity}
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); logger.exception('Cart update failed'); raise HTTPException(500,'Unable to update cart')
    finally:
        cur.close(); c.close()


@app.delete('/api/cart/{uid}/{product_id}')
def remove_cart_item(uid: int, product_id: int):

    require_customer(uid)
    c=db(); cur=c.cursor()
    try:
        cur.execute('DELETE ci FROM cart_items ci JOIN carts ca ON ca.id=ci.cart_id WHERE ca.user_id=%s AND ci.product_id=%s',(uid,product_id))
        c.commit(); return {'message':'Cart item removed'}
    finally:
        cur.close(); c.close()


@app.delete('/api/cart/{uid}')
def clear_cart(uid: int):

    require_customer(uid)
    c=db(); cur=c.cursor()
    try:
        cur.execute('DELETE ci FROM cart_items ci JOIN carts ca ON ca.id=ci.cart_id WHERE ca.user_id=%s',(uid,))
        c.commit(); return {'message':'Cart cleared'}
    finally:
        cur.close(); c.close()


@app.post('/api/orders/buy-cart/{uid}')
def buy_cart(uid: int, x: CartCheckoutIn):

    require_customer(uid)
    c=db(); cur=c.cursor(dictionary=True)
    try:
        cur.execute(
            'SELECT ci.product_id,ci.quantity,p.name,p.sku,p.price,p.stock_quantity,p.seller_id '
            'FROM carts ca JOIN cart_items ci ON ci.cart_id=ca.id '
            'JOIN products p ON p.id=ci.product_id '
            'WHERE ca.user_id=%s AND p.status=\'active\' ORDER BY ci.id FOR UPDATE', (uid,))
        items=cur.fetchall()
        if not items: raise HTTPException(400,'Your cart is empty')
        for item in items:
            if int(item['quantity']) < 1 or int(item['quantity']) > int(item['stock_quantity']):
                raise HTTPException(400,f"Insufficient stock for {item['name']}")

        aid = None
        if x.address_id:
            cur.execute('SELECT id FROM addresses WHERE id=%s AND user_id=%s LIMIT 1 FOR UPDATE',(x.address_id,uid))
            if cur.fetchone():
                aid = x.address_id
                cur.execute('UPDATE addresses SET address_type=%s,recipient_name=%s,phone=%s,address_line1=%s,address_line2=%s,city=%s,state=%s,postal_code=%s,country=%s,is_default=TRUE WHERE id=%s',(x.address.address_type,x.address.recipient_name,x.address.phone,x.address.address_line1,x.address.address_line2,x.address.city,x.address.state,x.address.postal_code,x.address.country,aid))
                cur.execute('UPDATE addresses SET is_default=FALSE WHERE user_id=%s AND id<>%s',(uid,aid))
        if aid is None:
            cur.execute('UPDATE addresses SET is_default=FALSE WHERE user_id=%s',(uid,))
            cur.execute('INSERT INTO addresses(user_id,address_type,recipient_name,phone,address_line1,address_line2,city,state,postal_code,country,is_default) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)',(uid,x.address.address_type,x.address.recipient_name,x.address.phone,x.address.address_line1,x.address.address_line2,x.address.city,x.address.state,x.address.postal_code,x.address.country))
            aid=cur.lastrowid
        subtotal=sum(float(i['price'])*int(i['quantity']) for i in items)
        order_no='HM'+datetime.utcnow().strftime('%Y%m%d%H%M%S')+secrets.token_hex(3).upper()
        cur.execute('INSERT INTO orders(user_id,address_id,order_number,subtotal,total_amount,status,payment_status) VALUES(%s,%s,%s,%s,%s,"confirmed","paid")',(uid,aid,order_no,subtotal,subtotal))
        oid=cur.lastrowid
        tx='DEMO-'+datetime.utcnow().strftime('%Y%m%d%H%M%S')+secrets.token_hex(4).upper()
        cur.execute('INSERT INTO payments(order_id,transaction_id,provider,amount,currency,status,paid_at) VALUES(%s,%s,%s,%s,%s,"success",UTC_TIMESTAMP())',(oid,tx,'HM Demo Payment',subtotal,'INR'))
        for item in items:
            q=int(item['quantity']); total=float(item['price'])*q
            cur.execute('INSERT INTO order_items(order_id,product_id,seller_id,product_name,sku,quantity,unit_price,total_amount) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(oid,item['product_id'],item['seller_id'],item['name'],item['sku'],q,item['price'],total))
            cur.execute('UPDATE products SET stock_quantity=stock_quantity-%s WHERE id=%s',(q,item['product_id']))
        cur.execute('INSERT INTO shipments(order_id,status) VALUES(%s,"pending")',(oid,))
        cur.execute('DELETE ci FROM cart_items ci JOIN carts ca ON ca.id=ci.cart_id WHERE ca.user_id=%s',(uid,))
        cur.execute('SELECT full_name,email FROM users WHERE id=%s LIMIT 1',(uid,))
        customer=cur.fetchone() or {}
        for item in items:
            cur.execute('''SELECT a.id admin_id,a.full_name admin_name,a.email admin_email FROM sellers ss JOIN users a ON a.id=ss.user_id WHERE ss.id=%s LIMIT 1''',(item.get('seller_id'),))
            owner=cur.fetchone() or {}
            audit_event(cur,'ORDER_CREATED',user_id=uid,order_id=oid,product_id=item['product_id'],details={'order_number':order_no,'customer_name':customer.get('full_name'),'customer_email':customer.get('email'),'product_name':item['name'],'sku':item['sku'],'quantity':item['quantity'],'unit_price':item['price'],'total_amount':float(item['price'])*int(item['quantity']),'product_admin_id':owner.get('admin_id'),'product_admin_name':owner.get('admin_name'),'product_admin_email':owner.get('admin_email')})
        audit_event(cur,'PAYMENT_SUCCESS',user_id=uid,order_id=oid,details={'order_number':order_no,'amount':subtotal,'payment_status':'paid','transaction_id':tx})
        c.commit()
        logger.info('ORDER_CREATED order_id=%s order_number=%s customer_id=%s items_count=%s amount=%s',oid,order_no,uid,len(items),subtotal)
        logger.info('PAYMENT_SUCCESS order_id=%s customer_id=%s amount=%s payment_status=paid transaction_id=%s',oid,uid,subtotal,tx)
        return {'message':'Demo payment successful. Order placed successfully','order_number':order_no,'order_id':oid,'total':subtotal,'payment_status':'paid','payment_status_detail':'success','transaction_id':tx,'items_count':len(items)}
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); logger.exception('Cart checkout failed'); raise HTTPException(500,'Unable to place cart order')
    finally:
        cur.close(); c.close()


# =========================================================
# BUY PRODUCT
# =========================================================

@app.post('/api/orders/buy/{uid}')
def buy(
    uid: int,
    x: BuyIn
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only normal customers can buy products'
        )

    if x.quantity < 1:

        raise HTTPException(
            400,
            'Quantity must be at least 1'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT p.*,s.id seller_id '
            'FROM products p '
            'LEFT JOIN sellers s ON s.id=p.seller_id '
            'WHERE p.id=%s '
            'AND p.status="active" '
            'FOR UPDATE',
            (x.product_id,)
        )

        p = cur.fetchone()

        if not p:

            raise HTTPException(
                404,
                'Product not found'
            )

        if (
            p['stock_quantity']
            < x.quantity
        ):

            raise HTTPException(
                400,
                'Insufficient stock'
            )


        # -------------------------------------------------
        # SAVE ADDRESS
        # -------------------------------------------------

        aid = None
        if x.address_id:
            cur.execute(
                'SELECT id FROM addresses WHERE id=%s AND user_id=%s LIMIT 1 FOR UPDATE',
                (x.address_id, uid)
            )
            if cur.fetchone():
                aid = x.address_id
                cur.execute(
                    'UPDATE addresses SET address_type=%s,recipient_name=%s,phone=%s,address_line1=%s,address_line2=%s,city=%s,state=%s,postal_code=%s,country=%s,is_default=TRUE WHERE id=%s',
                    (x.address.address_type,x.address.recipient_name,x.address.phone,x.address.address_line1,x.address.address_line2,x.address.city,x.address.state,x.address.postal_code,x.address.country,aid)
                )
                cur.execute('UPDATE addresses SET is_default=FALSE WHERE user_id=%s AND id<>%s',(uid,aid))
        if aid is None:
            cur.execute('UPDATE addresses SET is_default=FALSE WHERE user_id=%s',(uid,))
            cur.execute(
                'INSERT INTO addresses(user_id,address_type,recipient_name,phone,address_line1,address_line2,city,state,postal_code,country,is_default) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)',
                (uid,x.address.address_type,x.address.recipient_name,x.address.phone,x.address.address_line1,x.address.address2 if False else x.address.address_line2,x.address.city,x.address.state,x.address.postal_code,x.address.country)
            )
            aid = cur.lastrowid


        total = (
            float(p['price'])
            * x.quantity
        )


        order_no = (
            'HM'
            + datetime.utcnow().strftime(
                '%Y%m%d%H%M%S'
            )
            + secrets.token_hex(
                3
            ).upper()
        )


        cur.execute(
            'INSERT INTO orders('
            'user_id,address_id,order_number,subtotal,total_amount,'
            'status,payment_status'
            ') VALUES(%s,%s,%s,%s,%s,"confirmed","paid")',
            (
                uid,
                aid,
                order_no,
                total,
                total
            )
        )

        oid = cur.lastrowid


        transaction_id = (
            'DEMO-'
            + datetime.utcnow().strftime(
                '%Y%m%d%H%M%S'
            )
            + secrets.token_hex(
                4
            ).upper()
        )


        cur.execute(
            'INSERT INTO payments('
            'order_id,transaction_id,provider,amount,currency,status,paid_at'
            ') VALUES(%s,%s,%s,%s,%s,"success",UTC_TIMESTAMP())',
            (
                oid,
                transaction_id,
                'HM Demo Payment',
                total,
                'INR'
            )
        )


        cur.execute(
            'INSERT INTO order_items('
            'order_id,product_id,seller_id,product_name,sku,quantity,'
            'unit_price,total_amount'
            ') VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
            (
                oid,
                p['id'],
                p['seller_id'],
                p['name'],
                p['sku'],
                x.quantity,
                p['price'],
                total
            )
        )

        order_item_id = cur.lastrowid


        cur.execute(
            'UPDATE products '
            'SET stock_quantity=stock_quantity-%s '
            'WHERE id=%s',
            (
                x.quantity,
                p['id']
            )
        )


        # -------------------------------------------------
        # CREATE INITIAL SHIPMENT
        # -------------------------------------------------

        cur.execute(
            'INSERT INTO shipments('
            'order_id,carrier,tracking_number,status'
            ') VALUES(%s,NULL,NULL,"pending")',
            (oid,)
        )


        cur.execute('''SELECT a.id AS admin_id,a.full_name AS admin_name,a.email AS admin_email
                       FROM sellers s JOIN users a ON a.id=s.user_id WHERE s.id=%s LIMIT 1''',(p.get('seller_id'),))
        owner = cur.fetchone() or {}
        cur.execute('SELECT full_name,email FROM users WHERE id=%s LIMIT 1',(uid,))
        customer = cur.fetchone() or {}
        audit_event(cur, 'ORDER_CREATED', user_id=uid, order_id=oid, product_id=p['id'], details={
            'order_number': order_no, 'customer_name': customer.get('full_name'), 'customer_email': customer.get('email'),
            'product_name': p['name'], 'sku': p['sku'], 'quantity': x.quantity, 'unit_price': p['price'], 'total_amount': total,
            'product_admin_id': owner.get('admin_id'), 'product_admin_name': owner.get('admin_name'), 'product_admin_email': owner.get('admin_email')
        })
        audit_event(cur, 'PAYMENT_SUCCESS', user_id=uid, order_id=oid, product_id=p['id'], details={
            'order_number': order_no, 'amount': total, 'payment_method': getattr(x, 'payment_method', None), 'payment_status': 'paid', 'transaction_id': transaction_id
        })
        c.commit()

        logger.info(
            'ORDER_CREATED order_id=%s order_number=%s customer_id=%s customer_email=%s product_id=%s product_name=%s sku=%s quantity=%s amount=%s product_admin_id=%s product_admin_name=%s product_admin_email=%s',
            oid, order_no, uid, customer.get('email'), p['id'], p['name'], p['sku'], x.quantity, total, owner.get('admin_id'), owner.get('admin_name'), owner.get('admin_email')
        )
        logger.info(
            'PAYMENT_SUCCESS order_id=%s customer_id=%s amount=%s payment_status=paid transaction_id=%s',
            oid, uid, total, transaction_id
        )


        return {
            'message':
            'Demo payment successful. '
            'Order placed successfully',
            'order_number': order_no,
            'order_id': oid,
            'order_item_id': order_item_id,
            'total': total,
            'payment_status': 'paid',
            'payment_status_detail': 'success',
            'transaction_id': transaction_id
        }


    except Exception:

        c.rollback()
        raise


    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER ORDERS
# =========================================================

@app.get('/api/orders/{uid}')
def orders(
    uid: int
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can view customer orders'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'o.id,'
            'o.order_number,'
            'o.total_amount,'
            'o.status,'
            'o.payment_status,'
            'o.created_at,'
            'a.recipient_name,'
            'a.address_line1,'
            'a.city,'
            'a.state,'
            'a.postal_code,'
            'a.country,'
            's.carrier,'
            's.tracking_number,'
            's.status AS shipment_status,'
            's.shipped_at,'
            's.delivered_at,'
            'pmt.transaction_id,'
            'pmt.provider,'
            'pmt.status AS payment_record_status,'
            'pmt.paid_at,'
            'oi_summary.product_names,'
            'oi_summary.product_ids,'
            'oi_summary.admin_names AS product_admin_names '
            'FROM orders o '
            'JOIN addresses a ON a.id=o.address_id '
            'LEFT JOIN shipments s ON s.order_id=o.id '
            'LEFT JOIN payments pmt ON pmt.order_id=o.id '
            'LEFT JOIN ('
            'SELECT oi.order_id,'
            'GROUP_CONCAT(DISTINCT oi.product_name ORDER BY oi.id SEPARATOR \' | \') AS product_names,'
            'GROUP_CONCAT(DISTINCT oi.product_id ORDER BY oi.id SEPARATOR \' | \') AS product_ids,'
            'GROUP_CONCAT(DISTINCT COALESCE(au.full_name,\'Unknown admin\') ORDER BY oi.id SEPARATOR \' | \') AS admin_names '
            'FROM order_items oi '
            'LEFT JOIN sellers ss ON ss.id=oi.seller_id '
            'LEFT JOIN users au ON au.id=ss.user_id '
            'GROUP BY oi.order_id'
            ') oi_summary ON oi_summary.order_id=o.id '
            'WHERE o.user_id=%s '
            'ORDER BY o.id DESC',
            (uid,)
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER ORDER ITEMS
# =========================================================

@app.get('/api/orders/{uid}/{order_id}/items')
def customer_order_items(
    uid: int,
    order_id: int
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can view order items'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'oi.id,'
            'oi.order_id,'
            'oi.product_id,'
            'oi.seller_id,'
            'oi.product_name,'
            'oi.sku,'
            'oi.quantity,'
            'oi.unit_price,'
            'oi.total_amount '
            'FROM order_items oi '
            'JOIN orders o ON o.id=oi.order_id '
            'WHERE oi.order_id=%s '
            'AND o.user_id=%s '
            'ORDER BY oi.id',
            (
                order_id,
                uid
            )
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER CREATE RETURN REQUEST
# =========================================================

@app.post('/api/returns/{uid}')
def create_return_request(
    uid: int,
    x: ReturnRequestIn
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can request returns'
        )

    reason = x.reason.strip()

    if not reason:

        raise HTTPException(
            400,
            'Return reason is required'
        )

    if x.quantity < 1:

        raise HTTPException(
            400,
            'Return quantity must be at least 1'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        # -------------------------------------------------
        # VERIFY ORDER ITEM BELONGS TO CUSTOMER
        # -------------------------------------------------

        cur.execute(
            'SELECT '
            'oi.id,'
            'oi.order_id,'
            'oi.quantity,'
            'oi.unit_price,'
            'oi.total_amount,'
            'o.user_id,'
            'o.status AS order_status '
            'FROM order_items oi '
            'JOIN orders o ON o.id=oi.order_id '
            'WHERE oi.id=%s '
            'AND oi.order_id=%s '
            'AND o.user_id=%s '
            'FOR UPDATE',
            (
                x.order_item_id,
                x.order_id,
                uid
            )
        )

        item = cur.fetchone()

        if not item:

            raise HTTPException(
                404,
                'Order item not found'
            )

        if item['order_status'] != 'delivered':

            raise HTTPException(
                400,
                'Returns are available only after the order is delivered'
            )

        if x.quantity > item['quantity']:

            raise HTTPException(
                400,
                'Return quantity cannot exceed ordered quantity'
            )


        # -------------------------------------------------
        # CHECK EXISTING ACTIVE RETURN
        # -------------------------------------------------

        cur.execute(
            'SELECT '
            'id,'
            'quantity,'
            'status '
            'FROM return_requests '
            'WHERE order_item_id=%s '
            'AND user_id=%s '
            'AND status IN '
            '("requested","approved","received") '
            'FOR UPDATE',
            (
                x.order_item_id,
                uid
            )
        )

        existing = cur.fetchall()

        already_requested = sum(
            int(r['quantity'])
            for r in existing
        )

        if (
            already_requested
            + x.quantity
            > item['quantity']
        ):

            raise HTTPException(
                400,
                'Return quantity exceeds remaining returnable quantity'
            )


        refund_amount = (
            float(item['unit_price'])
            * x.quantity
        )


        cur.execute(
            'INSERT INTO return_requests('
            'order_id,'
            'order_item_id,'
            'user_id,'
            'quantity,'
            'reason,'
            'status,'
            'refund_amount,'
            'requested_at'
            ') VALUES(%s,%s,%s,%s,%s,"requested",%s,UTC_TIMESTAMP())',
            (
                x.order_id,
                x.order_item_id,
                uid,
                x.quantity,
                reason,
                refund_amount
            )
        )

        return_id = cur.lastrowid

        audit_event(cur,'RETURN_REQUESTED',user_id=uid,order_id=x.order_id,details={'return_request_id':return_id,'order_item_id':x.order_item_id,'quantity':x.quantity,'reason':reason})
        c.commit()

        logger.info(
            'customer user_id=%s created return_request_id=%s '
            'order_id=%s order_item_id=%s quantity=%s',
            uid,
            return_id,
            x.order_id,
            x.order_item_id,
            x.quantity
        )

        return {
            'message':
            'Return request submitted successfully',
            'id': return_id,
            'order_id': x.order_id,
            'order_item_id': x.order_item_id,
            'quantity': x.quantity,
            'status': 'requested',
            'refund_amount': refund_amount
        }

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Return request creation failed'
        )

        raise HTTPException(
            500,
            'Unable to create return request'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER RETURN REQUESTS
# =========================================================

@app.get('/api/returns/{uid}')
def customer_returns(
    uid: int
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can view return requests'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'rr.id,'
            'rr.order_id,'
            'rr.order_item_id,'
            'rr.user_id,'
            'rr.quantity,'
            'rr.reason,'
            'rr.status,'
            'rr.admin_note,'
            'rr.refund_amount,'
            'rr.requested_at,'
            'rr.processed_at,'
            'o.order_number,'
            'oi.product_name,'
            'oi.sku '
            'FROM return_requests rr '
            'JOIN orders o ON o.id=rr.order_id '
            'JOIN order_items oi ON oi.id=rr.order_item_id '
            'WHERE rr.user_id=%s '
            'ORDER BY rr.id DESC',
            (uid,)
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER CANCEL RETURN REQUEST
# =========================================================

@app.put('/api/returns/{uid}/{return_id}/cancel')
def cancel_return_request(
    uid: int,
    return_id: int
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can cancel return requests'
        )

    c = db()
    cur = c.cursor()

    try:

        cur.execute(
            'UPDATE return_requests '
            'SET status="cancelled", '
            'processed_at=UTC_TIMESTAMP() '
            'WHERE id=%s '
            'AND user_id=%s '
            'AND status="requested"',
            (
                return_id,
                uid
            )
        )

        if cur.rowcount == 0:

            c.rollback()

            raise HTTPException(
                400,
                'Return request cannot be cancelled'
            )

        c.commit()

        logger.info(
            'customer user_id=%s cancelled return_request_id=%s',
            uid,
            return_id
        )

        return {
            'message':
            'Return request cancelled'
        }

    finally:

        cur.close()
        c.close()


# =========================================================
# ADMIN - ALL RETURN REQUESTS
# =========================================================

@app.get('/api/admin/returns/{uid}')
def admin_returns(
    uid: int
):

    require_admin(uid)

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'rr.id,'
            'rr.order_id,'
            'rr.order_item_id,'
            'rr.user_id,'
            'rr.quantity,'
            'rr.reason,'
            'rr.status,'
            'rr.admin_note,'
            'rr.refund_amount,'
            'rr.requested_at,'
            'rr.processed_at,'
            'o.order_number,'
            'oi.product_name,'
            'oi.sku,'
            'u.full_name AS customer_name,'
            'u.email AS customer_email,'
            'u.phone AS customer_phone '
            'FROM return_requests rr '
            'JOIN orders o ON o.id=rr.order_id '
            'JOIN order_items oi ON oi.id=rr.order_item_id '
            'JOIN users u ON u.id=rr.user_id '
            'ORDER BY rr.id DESC'
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# ADMIN - PROCESS RETURN REQUEST
# =========================================================

@app.put('/api/admin/returns/{uid}/{return_id}')
def process_return_request(
    uid: int,
    return_id: int,
    x: ReturnProcessIn
):

    require_admin(uid)

    allowed_statuses = {
        'requested',
        'approved',
        'rejected',
        'received',
        'refunded',
        'cancelled'
    }

    status = x.status.strip().lower()

    if status not in allowed_statuses:

        raise HTTPException(
            400,
            'Invalid return status'
        )

    if (
        x.refund_amount is not None
        and x.refund_amount < 0
    ):

        raise HTTPException(
            400,
            'Refund amount cannot be negative'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'rr.*,'
            'o.order_number,'
            'oi.product_name,'
            'oi.unit_price '
            'FROM return_requests rr '
            'JOIN orders o ON o.id=rr.order_id '
            'JOIN order_items oi ON oi.id=rr.order_item_id '
            'WHERE rr.id=%s '
            'FOR UPDATE',
            (return_id,)
        )

        rr = cur.fetchone()

        if not rr:

            raise HTTPException(
                404,
                'Return request not found'
            )


        refund_amount = x.refund_amount

        if (
            refund_amount is None
            and status in (
                'approved',
                'received',
                'refunded'
            )
        ):

            refund_amount = (
                float(rr['unit_price'])
                * int(rr['quantity'])
            )


        cur.execute(
            'UPDATE return_requests '
            'SET status=%s,'
            'admin_note=%s,'
            'refund_amount=%s,'
            'processed_at=UTC_TIMESTAMP() '
            'WHERE id=%s',
            (
                status,
                x.admin_note,
                refund_amount,
                return_id
            )
        )


        # -------------------------------------------------
        # DEMO PAYMENT REFUND
        # -------------------------------------------------
        # When admin marks a return as refunded, update the
        # original payment record and order payment status.
        if status == 'refunded':

            if rr['status'] != 'refunded':
                cur.execute(
                    'UPDATE products p JOIN order_items oi ON oi.product_id=p.id '
                    'SET p.stock_quantity=p.stock_quantity+%s '
                    'WHERE oi.id=%s',
                    (rr['quantity'], rr['order_item_id'])
                )

            cur.execute(
                'UPDATE payments '
                'SET status="refunded" '
                'WHERE order_id=%s AND status="success"',
                (rr['order_id'],)
            )

            cur.execute(
                'UPDATE orders SET payment_status="refunded", status="returned" WHERE id=%s',
                (rr['order_id'],)
            )


        audit_event(cur,'RETURN_PROCESSED',admin_id=uid,order_id=rr['order_id'],details={'return_request_id':return_id,'order_item_id':rr.get('order_item_id'),'status':status,'refund_amount':refund_amount,'admin_id':uid})
        c.commit()

        logger.info(
            'admin user_id=%s processed return_request_id=%s '
            'status=%s refund_amount=%s',
            uid,
            return_id,
            status,
            refund_amount
        )

        return {
            'message':
            'Return request updated successfully',
            'id': return_id,
            'status': status,
            'refund_amount': refund_amount
        }

    except HTTPException:

        c.rollback()
        raise

    except Exception:

        c.rollback()

        logger.exception(
            'Return request processing failed'
        )

        raise HTTPException(
            500,
            'Unable to process return request'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# ADMIN - SHIPMENTS
# =========================================================

@app.get('/api/admin/shipments/{uid}')
def admin_shipments(
    uid: int
):

    require_admin(uid)

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            's.id,'
            's.order_id,'
            's.carrier,'
            's.tracking_number,'
            's.current_city,'
            's.current_location,'
            's.estimated_delivery,'
            's.status,'
            's.shipped_at,'
            's.delivered_at,'
            'o.order_number,'
            'o.user_id,'
            'u.full_name AS customer_name,'
            'u.email AS customer_email '
            'FROM shipments s '
            'JOIN orders o ON o.id=s.order_id '
            'JOIN users u ON u.id=o.user_id '
            'ORDER BY s.id DESC'
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# ADMIN - CREATE / UPDATE SHIPMENT
# =========================================================

@app.put('/api/admin/shipments/{uid}/{order_id}')
def update_shipment(
    uid: int,
    order_id: int,
    x: ShipmentIn
):

    require_admin(uid)

    allowed_statuses = {
        'pending',
        'packed',
        'shipped',
        'out_for_delivery',
        'delivered',
        'returned'
    }

    status = x.status.strip().lower()

    if status not in allowed_statuses:

        raise HTTPException(
            400,
            'Invalid shipment status'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        # -------------------------------------------------
        # CHECK ORDER
        # -------------------------------------------------

        cur.execute(
            'SELECT id FROM orders '
            'WHERE id=%s '
            'LIMIT 1',
            (order_id,)
        )

        order = cur.fetchone()

        if not order:

            raise HTTPException(
                404,
                'Order not found'
            )


        # -------------------------------------------------
        # CHECK EXISTING SHIPMENT
        # -------------------------------------------------

        cur.execute(
            'SELECT id FROM shipments '
            'WHERE order_id=%s '
            'LIMIT 1 '
            'FOR UPDATE',
            (order_id,)
        )

        shipment = cur.fetchone()


        shipped_at_sql = None
        delivered_at_sql = None

        if status in (
            'shipped',
            'out_for_delivery',
            'delivered'
        ):

            shipped_at_sql = 'UTC_TIMESTAMP()'


        if status == 'delivered':

            delivered_at_sql = 'UTC_TIMESTAMP()'


        if shipment:

            if status == 'delivered':

                cur.execute(
                    'UPDATE shipments '
                    'SET carrier=%s,'
                    'tracking_number=%s,'
                    'status=%s,'
                    'shipped_at=COALESCE('
                    'shipped_at,UTC_TIMESTAMP()'
                    '),'
                    'delivered_at=UTC_TIMESTAMP() '
                    'WHERE id=%s',
                    (
                        x.carrier,
                        x.tracking_number,
                        status,
                        shipment['id']
                    )
                )

            elif status in (
                'shipped',
                'out_for_delivery'
            ):

                cur.execute(
                    'UPDATE shipments '
                    'SET carrier=%s,'
                    'tracking_number=%s,'
                    'status=%s,'
                    'shipped_at=COALESCE('
                    'shipped_at,UTC_TIMESTAMP()'
                    ') '
                    'WHERE id=%s',
                    (
                        x.carrier,
                        x.tracking_number,
                        status,
                        shipment['id']
                    )
                )

            else:

                cur.execute(
                    'UPDATE shipments '
                    'SET carrier=%s,'
                    'tracking_number=%s,'
                    'status=%s '
                    'WHERE id=%s',
                    (
                        x.carrier,
                        x.tracking_number,
                        status,
                        shipment['id']
                    )
                )

        else:

            if status == 'delivered':

                cur.execute(
                    'INSERT INTO shipments('
                    'order_id,carrier,tracking_number,status,'
                    'shipped_at,delivered_at'
                    ') VALUES(%s,%s,%s,%s,'
                    'UTC_TIMESTAMP(),UTC_TIMESTAMP())',
                    (
                        order_id,
                        x.carrier,
                        x.tracking_number,
                        status
                    )
                )

            elif status in (
                'shipped',
                'out_for_delivery'
            ):

                cur.execute(
                    'INSERT INTO shipments('
                    'order_id,carrier,tracking_number,status,'
                    'shipped_at'
                    ') VALUES(%s,%s,%s,%s,UTC_TIMESTAMP())',
                    (
                        order_id,
                        x.carrier,
                        x.tracking_number,
                        status
                    )
                )

            else:

                cur.execute(
                    'INSERT INTO shipments('
                    'order_id,carrier,tracking_number,status'
                    ') VALUES(%s,%s,%s,%s)',
                    (
                        order_id,
                        x.carrier,
                        x.tracking_number,
                        status
                    )
                )


        # Save live delivery location and ETA for customer tracking.
        if shipment:
            cur.execute('UPDATE shipments SET current_city=%s,current_location=%s,estimated_delivery=%s WHERE id=%s',(x.current_city,x.current_location,x.estimated_delivery,shipment['id']))
        else:
            cur.execute('UPDATE shipments SET current_city=%s,current_location=%s,estimated_delivery=%s WHERE order_id=%s',(x.current_city,x.current_location,x.estimated_delivery,order_id))

        # Record every shipment update as a real tracking checkpoint.
        cur.execute(
            'INSERT INTO shipment_tracking_events(shipment_id,status,city,location,note) '
            'SELECT id,%s,%s,%s,%s FROM shipments WHERE order_id=%s LIMIT 1',
            (status, x.current_city, x.current_location, 'Shipment status updated by admin', order_id)
        )

        # -------------------------------------------------
        # KEEP ORDER STATUS IN SYNC
        # -------------------------------------------------

        order_status = None

        if status == 'delivered':

            order_status = 'delivered'

        elif status in (
            'shipped',
            'out_for_delivery'
        ):

            order_status = 'shipped'

        elif status == 'packed':

            order_status = 'packed'


        if order_status:

            cur.execute(
                'UPDATE orders '
                'SET status=%s '
                'WHERE id=%s',
                (
                    order_status,
                    order_id
                )
            )


        cur.execute('SELECT order_number,user_id FROM orders WHERE id=%s LIMIT 1',(order_id,))
        order_info = cur.fetchone() or {}
        audit_event(cur, 'SHIPMENT_UPDATED', admin_id=uid, order_id=order_id, details={
            'order_number': order_info.get('order_number'), 'customer_id': order_info.get('user_id'), 'status': status,
            'carrier': x.carrier, 'tracking_number': x.tracking_number, 'admin_id': uid
        })
        c.commit()

        logger.info(
            'admin user_id=%s updated shipment order_id=%s '
            'status=%s tracking=%s',
            uid,
            order_id,
            status,
            x.tracking_number
        )

        return {
            'message':
            'Shipment updated successfully',
            'order_id': order_id,
            'status': status,
            'carrier': x.carrier,
            'tracking_number':
            x.tracking_number
        }

    except HTTPException:

        c.rollback()
        raise

    except mysql.connector.IntegrityError:

        c.rollback()

        raise HTTPException(
            409,
            'Shipment already exists or order is invalid'
        )

    except Exception:

        c.rollback()

        logger.exception(
            'Shipment update failed'
        )

        raise HTTPException(
            500,
            'Unable to update shipment'
        )

    finally:

        cur.close()
        c.close()


# =========================================================
# CUSTOMER SHIPMENT TRACKING
# =========================================================

@app.get('/api/orders/{uid}/{order_id}/shipment')
def customer_shipment(
    uid: int,
    order_id: int
):

    u = user_by_id(uid)

    if (
        not u
        or u['role_name'] != 'customer'
    ):

        raise HTTPException(
            403,
            'Only customers can track shipments'
        )

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            's.id,'
            's.order_id,'
            's.carrier,'
            's.tracking_number,'
            's.status,'
            's.current_city,'
            's.current_location,'
            's.estimated_delivery,'
            's.shipped_at,'
            's.delivered_at '
            'FROM shipments s '
            'JOIN orders o ON o.id=s.order_id '
            'WHERE s.order_id=%s '
            'AND o.user_id=%s '
            'LIMIT 1',
            (
                order_id,
                uid
            )
        )

        shipment = cur.fetchone()

        if not shipment:

            raise HTTPException(
                404,
                'Shipment not found'
            )

        cur.execute(
            'SELECT status,city,location,note,created_at '
            'FROM shipment_tracking_events WHERE shipment_id=%s ORDER BY id ASC',
            (shipment['id'],)
        )
        shipment['events'] = cur.fetchall()
        return shipment

    finally:

        cur.close()
        c.close()


# =========================================================
# ADMIN - ORDER LIST
# =========================================================

@app.get('/api/admin/orders/{uid}')
def admin_orders(uid: int):
    require_admin(uid)
    c=db(); cur=c.cursor(dictionary=True)
    try:
        cur.execute("""SELECT o.id,o.order_number,o.user_id,o.total_amount,o.status,o.payment_status,o.created_at,
                              u.full_name AS customer_name,u.email AS customer_email,u.phone AS customer_phone,
                              s.carrier,s.tracking_number,s.current_city,s.current_location,s.estimated_delivery,s.status AS shipment_status,s.shipped_at,s.delivered_at,
                              pmt.transaction_id,pmt.provider,pmt.status AS payment_record_status,pmt.paid_at,
                              GROUP_CONCAT(DISTINCT oi.product_name ORDER BY oi.id SEPARATOR ' | ') AS product_names,
                              GROUP_CONCAT(DISTINCT oi.product_id ORDER BY oi.id SEPARATOR ' | ') AS product_ids,
                              GROUP_CONCAT(DISTINCT COALESCE(au.full_name,'Unknown admin') ORDER BY oi.id SEPARATOR ' | ') AS product_admin_names
                       FROM orders o JOIN users u ON u.id=o.user_id
                       LEFT JOIN shipments s ON s.order_id=o.id
                       LEFT JOIN payments pmt ON pmt.order_id=o.id
                       LEFT JOIN order_items oi ON oi.order_id=o.id
                       LEFT JOIN sellers ss ON ss.id=oi.seller_id
                       LEFT JOIN users au ON au.id=ss.user_id
                       GROUP BY o.id,o.order_number,o.user_id,o.total_amount,o.status,o.payment_status,o.created_at,u.full_name,u.email,u.phone,s.carrier,s.tracking_number,s.current_city,s.current_location,s.estimated_delivery,s.status,s.shipped_at,s.delivered_at,pmt.transaction_id,pmt.provider,pmt.status,pmt.paid_at
                       ORDER BY o.id DESC""")
        return cur.fetchall()
    finally:
        cur.close(); c.close()


# =========================================================
# ADMIN - ORDER DETAIL / TRACKING
# =========================================================

@app.get('/api/admin/orders/{uid}/{order_id}/details')
def admin_order_details(uid: int, order_id: int):
    require_admin(uid)
    c=db(); cur=c.cursor(dictionary=True)
    try:
        cur.execute('''SELECT o.id,o.order_number,o.user_id,o.address_id,o.subtotal,o.discount_amount,o.tax_amount,o.shipping_amount,o.total_amount,o.status,o.payment_status,o.created_at,o.updated_at,
                              u.full_name customer_name,u.email customer_email,u.phone customer_phone,
                              a.recipient_name,a.phone recipient_phone,a.address_line1,a.address_line2,a.city,a.state,a.postal_code,a.country,
                              s.id shipment_id,s.carrier,s.tracking_number,s.status shipment_status,s.current_city,s.current_location,s.estimated_delivery,s.shipped_at,s.delivered_at,
                              pm.id payment_id,pm.transaction_id,pm.provider,pm.amount payment_amount,pm.currency,pm.status payment_record_status,pm.paid_at
                       FROM orders o JOIN users u ON u.id=o.user_id JOIN addresses a ON a.id=o.address_id
                       LEFT JOIN shipments s ON s.order_id=o.id LEFT JOIN payments pm ON pm.order_id=o.id
                       WHERE o.id=%s LIMIT 1''',(order_id,))
        order=cur.fetchone()
        if not order: raise HTTPException(404,'Order not found')
        cur.execute('''SELECT oi.id,oi.product_id,oi.seller_id,oi.product_name,oi.sku,oi.quantity,oi.unit_price,oi.tax_amount,oi.total_amount,
                              au.id product_admin_id,au.full_name product_admin_name,au.email product_admin_email
                       FROM order_items oi LEFT JOIN sellers ss ON ss.id=oi.seller_id LEFT JOIN users au ON au.id=ss.user_id
                       WHERE oi.order_id=%s ORDER BY oi.id''',(order_id,))
        items=cur.fetchall()
        cur.execute('''SELECT rr.id return_request_id,rr.order_item_id,rr.quantity,rr.reason,rr.status return_status,rr.admin_note,rr.refund_amount,rr.requested_at,rr.processed_at,
                              au.id admin_id,au.full_name admin_name,au.email admin_email
                       FROM return_requests rr LEFT JOIN users au ON au.id=rr.admin_id WHERE rr.order_id=%s ORDER BY rr.id''',(order_id))
        returns=cur.fetchall()
        cur.execute('''SELECT ste.id,ste.status,ste.city,ste.location,ste.note,ste.created_at
                       FROM shipment_tracking_events ste JOIN shipments sx ON sx.id=ste.shipment_id
                       WHERE sx.order_id=%s ORDER BY ste.id ASC''',(order_id,))
        shipment_events=cur.fetchall()

        cur.execute('''SELECT ae.id,ae.event_type,ae.user_id,ae.admin_id,ae.product_id,ae.details,ae.created_at,
                              cu.full_name user_name,cu.email user_email,ad.full_name admin_name,ad.email admin_email
                       FROM audit_events ae LEFT JOIN users cu ON cu.id=ae.user_id LEFT JOIN users ad ON ad.id=ae.admin_id
                       WHERE ae.order_id=%s ORDER BY ae.id ASC''',(order_id,))
        events=cur.fetchall()
        for e in events:
            if isinstance(e.get('details'), str):
                try: e['details']=json.loads(e['details'])
                except Exception: pass
        return {'order':order,'items':items,'returns':returns,'events':events,'shipment_events':shipment_events}
    finally:
        cur.close(); c.close()


# =========================================================
# ADMIN - ORDER ITEMS
# =========================================================

@app.get('/api/admin/orders/{uid}/{order_id}/items')
def admin_order_items(
    uid: int,
    order_id: int
):

    require_admin(uid)

    c = db()
    cur = c.cursor(
        dictionary=True
    )

    try:

        cur.execute(
            'SELECT '
            'oi.id,'
            'oi.order_id,'
            'oi.product_id,'
            'oi.seller_id,'
            'oi.product_name,'
            'oi.sku,'
            'oi.quantity,'
            'oi.unit_price,'
            'oi.total_amount '
            'FROM order_items oi '
            'WHERE oi.order_id=%s '
            'ORDER BY oi.id',
            (order_id,)
        )

        return cur.fetchall()

    finally:

        cur.close()
        c.close()


# =========================================================
# ROOT
# =========================================================

@app.get('/')
def root():

    return {
        'message':
        'HM Shopping Mart API is running',
        'status':
        'ok'
    }

