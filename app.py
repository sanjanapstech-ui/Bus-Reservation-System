from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse, unquote
import qrcode
from io import BytesIO
import json
import pymysql
from pymysql import IntegrityError, Error as PyMySQLError

# Load environment variables from .env file (only for local development)
# On Vercel/Render, environment variables are set in the dashboard
try:
    from dotenv import load_dotenv
    load_dotenv()  # This will silently fail if .env doesn't exist (which is fine for Vercel)
except ImportError:
    pass  # python-dotenv not available, use system environment variables


def is_production_env() -> bool:
    env = (os.getenv("ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    if env in {"development", "dev", "local"}:
        return False
    if env in {"production", "prod"}:
        return True

    # Heuristics for common deployment platforms
    return bool(
        os.getenv("RENDER")
        or os.getenv("RENDER_SERVICE_ID")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("VERCEL")
        or os.getenv("VERCEL_ENV")
        or os.getenv("VERCEL_URL")
    )


app = Flask(__name__)

# Secret key is required for sessions
_secret_key = os.getenv("SECRET_KEY")
if not _secret_key:
    # If the app is imported (gunicorn/serverless), don't allow an insecure default.
    # For `python app.py` local dev, fall back to a fixed dev key.
    if __name__ != "__main__":
        raise RuntimeError(
            "SECRET_KEY environment variable must be set. "
            "For local dev, copy .env.example to .env. For production, set it in your hosting dashboard."
        )
    _secret_key = "dev-secret-key-change-in-production"
app.secret_key = _secret_key

# Cookie hardening
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_production_env(),
)


def require_diagnostics_token(view_func):
    """Protect diagnostics routes in production.

    In debug mode, access is always allowed. In production, set DIAGNOSTICS_TOKEN and
    pass it via `?token=...` or `X-DIAGNOSTICS-TOKEN` header.
    """

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if app.debug:
            return view_func(*args, **kwargs)

        token = os.getenv("DIAGNOSTICS_TOKEN")
        if not token:
            return ("Not Found", 404)

        provided = (
            request.headers.get("X-DIAGNOSTICS-TOKEN")
            or request.headers.get("X-DIAGNOSTICS_TOKEN")
            or request.args.get("token")
        )
        if provided != token:
            return ("Not Found", 404)

        return view_func(*args, **kwargs)

    return wrapper


def require_debug_mode(view_func):
    """Disable debug-only routes in production."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not app.debug:
            return ("Not Found", 404)
        return view_func(*args, **kwargs)

    return wrapper


def env_first(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def parse_mysql_url(mysql_url):
    """Parse a MySQL URL like: mysql://user:pass@host:3306/dbname"""
    if not mysql_url:
        return None

    try:
        parsed = urlparse(mysql_url)
    except Exception:
        return None

    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        return None

    database = parsed.path.lstrip("/") if parsed.path else ""
    return {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password is not None else None,
        "database": database or None,
    }

# MySQL Configuration using PyMySQL
class MySQL:
    """Custom MySQL wrapper using PyMySQL to replace Flask-MySQLdb"""
    def __init__(self, app=None):
        self.app = app
        self._connection = None
        self.config = {}
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        self.app = app
        self.config = {
            'host': app.config.get('MYSQL_HOST', 'localhost'),
            'user': app.config.get('MYSQL_USER', 'root'),
            'password': app.config.get('MYSQL_PASSWORD', ''),
            'database': app.config.get('MYSQL_DB', ''),
            'port': app.config.get('MYSQL_PORT', 3306),
            'charset': 'utf8mb4',
            'autocommit': False,
            'connect_timeout': 10
        }
    
    def connect(self):
        """Create a new database connection"""
        try:
            # Check if we're in production and database config is missing
            if is_production_env() and self.config.get('host') in {'localhost', '127.0.0.1', '::1'}:
                raise ConnectionError(
                    "Database configuration error: In production, MYSQL_HOST environment variable must be set. "
                    "Please set MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, and MYSQL_DB in your deployment environment."
                )
            
            self._connection = pymysql.connect(**self.config)
            print(f"Successfully connected to MySQL at {self.config.get('host')}:{self.config.get('port')}")
        except pymysql.Error as e:
            error_msg = str(e)
            print(f"MySQL connection error: {error_msg}")
            # Provide helpful error messages
            if 'Connection refused' in error_msg or 'Can\'t connect' in error_msg:
                if self.config.get('host') in {'localhost', '127.0.0.1', '::1'}:
                    raise ConnectionError(
                        "Cannot connect to database on localhost. "
                        "In production, you must set MYSQL_HOST environment variable to your database server address. "
                        f"Current host: {self.config.get('host')}"
                    )
                else:
                    raise ConnectionError(
                        f"Cannot connect to MySQL server at {self.config.get('host')}:{self.config.get('port')}. "
                        "Please check: 1) Database server is running, 2) Host and port are correct, "
                        "3) Network/firewall allows connections, 4) Database credentials are correct."
                    )
            raise
        except Exception as e:
            print(f"Error connecting to MySQL: {str(e)}")
            raise
    
    def get_connection(self):
        """Get or create database connection (serverless-friendly)"""
        try:
            if self._connection is None:
                self.connect()
            else:
                # Test if connection is still alive
                # In serverless, connections may be closed between invocations
                try:
                    self._connection.ping(reconnect=False)
                except Exception:
                    # Connection lost, reconnect
                    self._connection = None
                    self.connect()
        except Exception as e:
            # Connection lost, reconnect
            print(f"Connection error, reconnecting: {str(e)}")
            self._connection = None
            try:
                self.connect()
            except Exception as reconnect_error:
                # If reconnection fails, raise a more descriptive error
                print(f"Failed to reconnect to database: {str(reconnect_error)}")
                raise ConnectionError(f"Database connection failed: {str(reconnect_error)}") from reconnect_error
        return self._connection
    
    def commit(self):
        """Commit the current transaction"""
        conn = self.get_connection()
        if conn:
            conn.commit()
    
    def rollback(self):
        """Rollback the current transaction"""
        conn = self.get_connection()
        if conn:
            conn.rollback()
    
    def close(self):
        """Close the database connection"""
        if self._connection:
            try:
                self._connection.close()
            except:
                pass
            self._connection = None

# MySQL Configuration
# In production (Render/Vercel), these MUST be set as environment variables
# For local development, you can use .env file or defaults
_mysql_url = env_first("MYSQL_URL", "DATABASE_URL")
_mysql_url_parts = parse_mysql_url(_mysql_url)

app.config['MYSQL_HOST'] = (
    (_mysql_url_parts.get("host") if _mysql_url_parts else None)
    or env_first("MYSQL_HOST", "MYSQLHOST", "DB_HOST", "DBHOST", default="localhost")
)
app.config['MYSQL_USER'] = (
    (_mysql_url_parts.get("user") if _mysql_url_parts else None)
    or env_first("MYSQL_USER", "MYSQLUSER", "DB_USER", "DBUSER", default="root")
)
_mysql_url_password = _mysql_url_parts.get("password") if _mysql_url_parts else None
app.config['MYSQL_PASSWORD'] = (
    _mysql_url_password
    if _mysql_url_password is not None
    else env_first("MYSQL_PASSWORD", "MYSQLPASSWORD", "DB_PASSWORD", "DBPASSWORD", default="")
)
app.config['MYSQL_DB'] = (
    (_mysql_url_parts.get("database") if _mysql_url_parts else None)
    or env_first("MYSQL_DB", "MYSQLDATABASE", "DB_NAME", "DBNAME", default="bus_management")
)
app.config['MYSQL_PORT'] = int(
    (_mysql_url_parts.get("port") if _mysql_url_parts else None)
    or env_first("MYSQL_PORT", "MYSQLPORT", "DB_PORT", "DBPORT", default=3306)
)

mysql = MySQL(app)

# Connection wrapper to make mysql.connection work like Flask-MySQLdb
class ConnectionWrapper:
    """Wrapper to make mysql.connection work like Flask-MySQLdb"""
    def __init__(self, mysql_instance):
        self.mysql = mysql_instance
    
    def cursor(self):
        """Get a cursor from the connection"""
        try:
            conn = self.mysql.get_connection()
            return conn.cursor()
        except Exception as e:
            print(f"Error getting cursor: {str(e)}")
            raise
    
    def commit(self):
        """Commit the transaction"""
        self.mysql.commit()
    
    def rollback(self):
        """Rollback the transaction"""
        self.mysql.rollback()

# Replace mysql.connection with wrapper
mysql.connection = ConnectionWrapper(mysql)

# Create required tables if they don't exist
def init_db():
    with app.app_context():
        cur = mysql.connection.cursor()
        try:
            # Core tables
            cur.execute('''
                CREATE TABLE IF NOT EXISTS `user` (
                    `id` INT NOT NULL AUTO_INCREMENT,
                    `usn` VARCHAR(20) NOT NULL,
                    `name` VARCHAR(100) NOT NULL,
                    `phone` VARCHAR(15) NOT NULL,
                    `email` VARCHAR(100) NOT NULL,
                    `password` VARCHAR(255) NOT NULL,
                    `bus_number` VARCHAR(20) DEFAULT NULL,
                    `address` TEXT NOT NULL,
                    `distance` FLOAT DEFAULT NULL,
                    `balance` DECIMAL(10,2) DEFAULT 0.00,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `usn` (`usn`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS `bus` (
                    `id` INT NOT NULL AUTO_INCREMENT,
                    `bus_number` VARCHAR(20) NOT NULL,
                    `starting_point` VARCHAR(100) NOT NULL,
                    `ending_point` VARCHAR(100) NOT NULL,
                    `available_seats` INT NOT NULL,
                    `total_seats` INT NOT NULL,
                    `fare` DECIMAL(10,2) NOT NULL,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `bus_number` (`bus_number`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # Seed bus routes for demos (only inserts if missing)
            default_buses = [
                ("1", "Kundapura", "SMVITM College", 10, 40, 30.00),
                ("2", "Udupi", "SMVITM College", 7, 35, 20.00),
                ("3", "Manipal", "SMVITM College", 20, 45, 25.00),
                ("4", "Brahmavar", "SMVITM College", 6, 40, 28.00),
                ("5", "Mangalore", "SMVITM College", 15, 50, 35.00),
            ]
            for bus in default_buses:
                cur.execute(
                    '''
                    INSERT IGNORE INTO `bus`
                    (bus_number, starting_point, ending_point, available_seats, total_seats, fare)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ''',
                    bus,
                )

            # Create transactions table (never drop in app runtime)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    transaction_type ENUM('credit', 'debit') NOT NULL,
                    description VARCHAR(255),
                    bus_number VARCHAR(20),
                    location VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES `user`(id)
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS notification (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    message TEXT NOT NULL,
                    is_read TINYINT(1) DEFAULT 0,
                    requires_response TINYINT(1) DEFAULT 0,
                    response VARCHAR(10) DEFAULT NULL,
                    KEY user_id (user_id),
                    CONSTRAINT notification_ibfk_1 FOREIGN KEY (user_id) REFERENCES `user`(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            # Create feedback table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    feedback_type ENUM('service', 'bus', 'driver', 'schedule', 'other') NOT NULL,
                    rating INT NOT NULL,
                    feedback_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES `user`(id)
                )
            ''')
            mysql.connection.commit()
            print("Database tables initialized successfully")
        except Exception as e:
            print(f"Error creating tables: {str(e)}")
            try:
                mysql.connection.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()

# Initialize database tables only once (lazy initialization for serverless)
# Don't call init_db() at module level - it will fail in serverless cold starts
# Instead, initialize on first request or use a separate migration script
_db_initialized = False

def ensure_db_initialized():
    """Lazy initialization of database tables - only runs when needed"""
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
            _db_initialized = True
        except Exception as e:
            print(f"Warning: Database initialization failed: {str(e)}")
            # Don't fail the app if DB init fails - tables might already exist

# Flask before_request hook to ensure DB is initialized for routes that need it
@app.before_request
def before_request():
    """Ensure database is initialized before handling requests"""
    # Only initialize if we're accessing a route that needs the database
    # Skip for static files and simple routes
    # Wrap in try-except to prevent function invocation failures
    try:
        if request.endpoint and request.endpoint not in ['static', 'generate_qr', 'index']:
            ensure_db_initialized()
    except Exception as e:
        # Log error but don't fail the request - let individual routes handle DB errors
        print(f"Warning: Database initialization skipped: {str(e)}")
        # Don't raise - allow the request to continue
        # Individual routes will handle their own DB connection errors

# Helper function to calculate distance
def calculate_distance(address):
    distances = {
        'Kundapura': 30,
        'Udupi': 8.5,
        'Manipal': 12,
        'Brahmavar': 15,
        'Mangalore': 25
    }
    for location, distance in distances.items():
        if location.lower() in address.lower():
            return distance
    return 10

@app.route('/')
def index():
    return render_template('base.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        usn = None
        email = None
        cur = None
        try:
            usn = request.form.get('usn', '').strip()
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            bus_number = request.form.get('bus_number', '')
            address = request.form.get('address', '').strip()
            
            # Validate required fields
            if not usn:
                flash('USN is required. Please enter your USN.', 'error')
                return render_template('register.html')
            if not name:
                flash('Full name is required. Please enter your name.', 'error')
                return render_template('register.html')
            if not phone:
                flash('Phone number is required. Please enter your phone number.', 'error')
                return render_template('register.html')
            if not email:
                flash('Email is required. Please enter your email address.', 'error')
                return render_template('register.html')
            if not password:
                flash('Password is required. Please enter a password.', 'error')
                return render_template('register.html')
            if not address:
                flash('Address is required. Please enter your address.', 'error')
                return render_template('register.html')
            
            # Validate password length
            if len(password) < 6:
                flash('Password must be at least 6 characters long.', 'error')
                return render_template('register.html')
            
            distance = calculate_distance(address)
            hashed_password = generate_password_hash(password)
            
            # Get database connection
            try:
                cur = mysql.connection.cursor()
            except ConnectionError as db_conn_error:
                error_msg = str(db_conn_error)
                print(f"Database connection error: {error_msg}")
                if 'localhost' in error_msg or 'environment variable' in error_msg.lower():
                    flash('Database configuration error: Database connection settings are missing. Please contact the administrator.', 'error')
                else:
                    flash('Cannot connect to database. Please try again later or contact support.', 'error')
                return render_template('register.html')
            except Exception as db_conn_error:
                error_msg = str(db_conn_error)
                print(f"Database connection error: {error_msg}")
                if 'localhost' in error_msg or 'Connection refused' in error_msg:
                    flash('Database configuration error: Cannot connect to database server. Please contact the administrator.', 'error')
                else:
                    flash('Cannot connect to database. Please try again later or contact support.', 'error')
                return render_template('register.html')
            
            # Check if USN already exists (case-insensitive check)
            try:
                cur.execute('SELECT id, usn FROM user WHERE UPPER(usn) = UPPER(%s)', (usn,))
                existing_user = cur.fetchone()
                if existing_user:
                    existing_usn = existing_user[1] if len(existing_user) > 1 else usn
                    cur.close()
                    flash(f'USN "{existing_usn}" is already registered. If this is your USN, please login instead. Otherwise, use a different USN.', 'error')
                    return render_template('register.html')
            except Exception as check_error:
                print(f"Error checking USN: {str(check_error)}")
                cur.close()
                flash('Error checking USN. Please try again.', 'error')
                return render_template('register.html')
            
            # Check if email already exists
            try:
                cur.execute('SELECT id FROM user WHERE email = %s', (email,))
                if cur.fetchone():
                    cur.close()
                    flash(f'Email "{email}" is already registered. Please use a different email or login.', 'error')
                    return render_template('register.html')
            except Exception as check_error:
                print(f"Error checking email: {str(check_error)}")
                cur.close()
                flash('Error checking email. Please try again.', 'error')
                return render_template('register.html')
            
            # Insert new user
            try:
                cur.execute('''
                    INSERT INTO user (usn, name, phone, email, password, bus_number, address, distance, balance)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (usn, name, phone, email, hashed_password, bus_number, address, distance, 0))
                mysql.connection.commit()
                cur.close()
                
                flash('Registration successful! Please login.', 'success')
                return redirect(url_for('login'))
            except IntegrityError as e:
                mysql.connection.rollback()
                error_msg = str(e).lower()
                print(f"Integrity error: {error_msg}")
                if 'duplicate' in error_msg or 'unique' in error_msg:
                    if 'usn' in error_msg or '1062' in error_msg:
                        flash(f'USN "{usn}" is already registered. Please use a different USN or login.', 'error')
                    elif 'email' in error_msg:
                        flash(f'Email "{email}" is already registered. Please use a different email or login.', 'error')
                    else:
                        flash('This information is already registered. Please check your details and try again.', 'error')
                else:
                    flash('Registration failed due to database constraint. Please check your information.', 'error')
                if cur:
                    cur.close()
                return render_template('register.html')
            except PyMySQLError as e:
                mysql.connection.rollback()
                print(f"Database error during registration: {str(e)}")
                error_msg = str(e)
                if 'connection' in error_msg.lower():
                    flash('Database connection error. Please try again later.', 'error')
                else:
                    flash(f'Database error: {error_msg}. Please try again.', 'error')
                if cur:
                    cur.close()
                return render_template('register.html')
        except KeyError as e:
            # Missing form field
            missing_field = str(e).replace("'", "")
            flash(f'Missing required field: {missing_field}. Please fill in all fields.', 'error')
            if cur:
                cur.close()
            return render_template('register.html')
        except Exception as e:
            # Handle any other errors
            error_msg = str(e)
            print(f"Unexpected error during registration: {error_msg}")
            print(f"Error type: {type(e).__name__}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            if cur:
                try:
                    mysql.connection.rollback()
                    cur.close()
                except:
                    pass
            flash(f'Registration failed: {error_msg}. Please check all fields and try again.', 'error')
            return render_template('register.html')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usn = request.form['usn']
        password = request.form['password']
        
        cur = mysql.connection.cursor()
        cur.execute('SELECT * FROM user WHERE usn = %s', (usn,))
        user = cur.fetchone()
        cur.close()
        
        if user and check_password_hash(user[5], password):
            session['user_id'] = user[0]
            session['usn'] = user[1]
            session['name'] = user[2]
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid USN or password', 'error')
    
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    
    try:
        # Get user details
        cur.execute('''
            SELECT id, usn, name, phone, email, bus_number, address, balance 
            FROM user 
            WHERE id = %s
        ''', (session['user_id'],))
        user = cur.fetchone()
        
        if not user:
            flash('User not found', 'error')
            return redirect(url_for('login'))
        
        # Get all available buses
        cur.execute('''
            SELECT bus_number, starting_point, ending_point, total_seats, available_seats, fare
            FROM bus 
            WHERE available_seats > 0
        ''')
        buses = cur.fetchall()
        
        # Convert user tuple to dictionary for easier template access
        user_dict = {
            'id': user[0],
            'usn': user[1],
            'name': user[2],
            'phone': user[3],
            'email': user[4],
            'bus_number': user[5],
            'address': user[6],
            'balance': user[7]
        }
        
        # Convert bus tuples to dictionaries
        bus_list = []
        for bus in buses:
            bus_dict = {
                'bus_number': bus[0],
                'starting_point': bus[1],
                'ending_point': bus[2],
                'total_seats': bus[3],
                'seats_left': bus[4],
                'fare': bus[5]
            }
            bus_list.append(bus_dict)
        
        return render_template('dashboard.html', user=user_dict, buses=bus_list)
        
    except Exception as e:
        print(f"Error in dashboard: {str(e)}")
        flash('An error occurred while loading the dashboard', 'error')
        return redirect(url_for('login'))
    finally:
        cur.close()

@app.route('/topup', methods=['GET', 'POST'])
def topup():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Get current balance
        cur.execute('SELECT balance FROM user WHERE id = %s', (session['user_id'],))
        result = cur.fetchone()
        if not result:
            flash('User not found', 'error')
            return redirect(url_for('login'))
        
        current_balance = float(result[0]) if result[0] is not None else 0.0
        
        if request.method == 'POST':
            try:
                amount = float(request.form['amount'])
                payment_method = request.form.get('payment_method', 'UPI')
                
                if amount <= 0:
                    flash('Please enter a valid amount greater than 0', 'error')
                    return render_template('topup.html', balance=current_balance)
                
                # Update balance
                new_balance = current_balance + amount
                cur.execute('UPDATE user SET balance = %s WHERE id = %s', (new_balance, session['user_id']))
                
                # Add to transaction history
                cur.execute('''
                    INSERT INTO transactions 
                    (user_id, amount, transaction_type, description, bus_number, location, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ''', (session['user_id'], amount, 'credit', f'Top up via {payment_method}', 'N/A', 'N/A'))
                
                mysql.connection.commit()
                
                # Update session with new balance
                session['balance'] = new_balance
                
                # Show success message and stay on the same page
                flash(f'Top up successful! ₹{amount:.2f} added to your account. New balance: ₹{new_balance:.2f}', 'success')
                return render_template('topup.html', balance=new_balance)
                
            except ValueError:
                flash('Please enter a valid amount', 'error')
            except Exception as e:
                print(f"Error in topup: {str(e)}")
                flash('An error occurred. Please try again.', 'error')
                mysql.connection.rollback()
        
        return render_template('topup.html', balance=current_balance)
    except Exception as e:
        print(f"Error in topup route: {str(e)}")
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()

@app.route('/qr_scan')
def qr_scan():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('qr.html')

@app.route('/view_transactions')
def view_transactions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Get all transactions with full details
        cur.execute('''
            SELECT 
                id,
                amount,
                transaction_type,
                description,
                bus_number,
                location,
                created_at
            FROM transactions 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        ''', (session['user_id'],))
        
        transactions = []
        for t in cur.fetchall():
            transaction = {
                'id': t[0],
                'amount': float(t[1]),
                'transaction_type': t[2],
                'description': t[3],
                'bus_number': t[4],
                'location': t[5],
                'created_at': t[6]
            }
            transactions.append(transaction)
        
        if not transactions:
            flash('No transactions found', 'info')
        
        return render_template('transaction.html', transactions=transactions)
    except Exception as e:
        print(f"Error in view_transactions: {str(e)}")  # Debug logging
        flash('An error occurred while loading transactions', 'error')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()

@app.route('/view_bus_location')
def view_bus_location():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('map.html')

@app.route('/book_bus/<int:bus_id>', methods=['GET', 'POST'])
def book_bus(bus_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Get bus details
        cur.execute('SELECT * FROM bus WHERE bus_number = %s', (bus_id,))
        bus = cur.fetchone()
        
        if not bus:
            flash('Bus not found', 'error')
            return redirect(url_for('dashboard'))
        
        if request.method == 'POST':
            try:
                seats = int(request.form['seats'])
                
                # Validate seats
                if seats <= 0:
                    flash('Please enter a valid number of seats', 'error')
                    return render_template('booking.html', bus=bus)
                
                if seats > bus[4]:  # available_seats
                    flash(f'Only {bus[4]} seats available', 'error')
                    return render_template('booking.html', bus=bus)
                
                # Update available seats
                cur.execute('UPDATE bus SET available_seats = available_seats - %s WHERE bus_number = %s', 
                          (seats, bus_id))
                
                mysql.connection.commit()
                
                # Show success message and updated bus info
                flash(f'Successfully booked {seats} seat(s) for Bus {bus_id}! Remember to scan the QR code at the bus stop to pay the fare.', 'success')
                
                # Get updated bus info
                cur.execute('SELECT * FROM bus WHERE bus_number = %s', (bus_id,))
                updated_bus = cur.fetchone()
                
                return render_template('booking.html', bus=updated_bus)
                
            except ValueError:
                flash('Please enter a valid number of seats', 'error')
            except Exception as e:
                print(f"Error in booking: {str(e)}")
                flash('An error occurred while booking. Please try again.', 'error')
                mysql.connection.rollback()
        
        return render_template('booking.html', bus=bus)
        
    except Exception as e:
        print(f"Error in book_bus route: {str(e)}")
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/generate-qr')
def generate_qr():
    # Create QR code with bus information
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    
    # Bus information to encode
    bus_info = {
        'bus_number': '1',
        'location': 'Udupi'
    }
    
    # Convert to JSON string
    qr.add_data(json.dumps(bus_info))
    qr.make(fit=True)
    
    # Create an image from the QR Code
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save the image to a BytesIO object
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    return send_file(img_io, mimetype='image/png')

@app.route('/scan-qr', methods=['POST'])
def scan_qr():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login first'})
    
    try:
        data = request.get_json()
        if not data or 'bus_number' not in data:
            return jsonify({'success': False, 'message': 'Invalid request data'})
        
        # Parse the QR code data safely
        try:
            # The QR code data should be a string representation of a dictionary
            bus_info_str = data['bus_number'].replace("'", '"')  # Replace single quotes with double quotes
            bus_info = json.loads(bus_info_str)
        except json.JSONDecodeError:
            return jsonify({'success': False, 'message': 'Invalid QR code format'})
        
        if not isinstance(bus_info, dict) or 'bus_number' not in bus_info:
            return jsonify({'success': False, 'message': 'Invalid QR code data'})
        
        cur = mysql.connection.cursor()
        
        # Get user's current balance
        cur.execute('SELECT balance FROM user WHERE id = %s', (session['user_id'],))
        user_data = cur.fetchone()
        if not user_data:
            return jsonify({'success': False, 'message': 'User not found'})
        
        current_balance = float(user_data[0]) if user_data[0] is not None else 0.0
        
        # Get bus fare from database
        cur.execute('SELECT fare FROM bus WHERE bus_number = %s', (bus_info['bus_number'],))
        bus_data = cur.fetchone()
        if not bus_data:
            return jsonify({'success': False, 'message': 'Bus not found'})
        
        fare = float(bus_data[0])
        location = bus_info.get('location', 'Unknown')
        bus_number = bus_info.get('bus_number', 'Unknown')
        
        if current_balance < fare:
            return jsonify({'success': False, 'message': f'Insufficient balance. Required: ₹{fare}, Available: ₹{current_balance}'})
        
        # Deduct fare and record transaction
        new_balance = current_balance - fare
        cur.execute('UPDATE user SET balance = %s WHERE id = %s', (new_balance, session['user_id']))
        
        # Record transaction
        cur.execute('''
            INSERT INTO transactions (user_id, amount, transaction_type, description, bus_number, location)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (session['user_id'], fare, 'debit', f'Bus fare payment - {location}', bus_number, location))
        
        mysql.connection.commit()
        cur.close()
        
        return jsonify({
            'success': True, 
            'message': f'Fare of ₹{fare} deducted successfully for Bus {bus_number} from {location}'
        })
        
    except Exception as e:
        print(f"Error in scan_qr: {str(e)}")
        message = "Error processing QR code. Please try again."
        if not is_production_env():
            message = f"Error processing QR code: {str(e)}"
        return jsonify({'success': False, 'message': message})

@app.route('/view-qr-code')
def view_qr_code():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('qr_code.html')

@app.route('/respond-notification', methods=['POST'])
def respond_notification():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    notification_id = request.form.get('notification_id')
    response = request.form.get('response')
    
    cur = mysql.connection.cursor()
    try:
        if response == 'yes':
            bus_number = session.get('bus_number')
            cur.execute(
                'SELECT starting_point, ending_point FROM bus WHERE bus_number = %s',
                (bus_number,),
            )
            bus_row = cur.fetchone()
            if not bus_row:
                flash('Bus not found', 'error')
                return redirect(url_for('notification'))

            route_from, route_to = bus_row[0], bus_row[1]

            # Try to reserve a seat atomically
            cur.execute(
                'UPDATE bus SET available_seats = available_seats - 1 WHERE bus_number = %s AND available_seats > 0',
                (bus_number,),
            )

            if cur.rowcount == 1:
                mysql.connection.commit()
                flash('Your seat has been confirmed!', 'success')
            else:
                # Show alternative buses
                cur.execute(
                    'SELECT * FROM bus WHERE starting_point = %s AND ending_point = %s AND bus_number != %s AND available_seats > 0',
                    (route_from, route_to, bus_number),
                )
                alternative_buses = cur.fetchall()
                flash('Your regular bus is full. Please select an alternative bus.', 'warning')
                return render_template('notification.html', alternative_buses=alternative_buses)
        else:
            flash('You have declined to board the bus today.', 'info')
        
        return redirect(url_for('notification'))
        
    except Exception as e:
        print(f"Error in respond_notification: {str(e)}")
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('notification'))
    finally:
        cur.close()

@app.route('/select-alternative-bus/<int:bus_number>', methods=['POST'])
def select_alternative_bus(bus_number):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Check if seats are available in the alternative bus
        cur.execute(
            'UPDATE bus SET available_seats = available_seats - 1 WHERE bus_number = %s AND available_seats > 0',
            (bus_number,),
        )

        if cur.rowcount == 1:
            # Assign the user to the alternative bus
            cur.execute('UPDATE user SET bus_number = %s WHERE id = %s', (bus_number, session['user_id']))
            mysql.connection.commit()
            session['bus_number'] = str(bus_number)
            flash(f'Successfully booked seat in Bus {bus_number}!', 'success')
        else:
            flash('Sorry, this bus is now full. Please try another alternative.', 'error')
        
        return redirect(url_for('notification'))
        
    except Exception as e:
        print(f"Error in select_alternative_bus: {str(e)}")
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('notification'))
    finally:
        cur.close()

@app.route('/notification')
def notification():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Get user's bus details
        cur.execute('SELECT bus_number FROM user WHERE id = %s', (session['user_id'],))
        user_bus = cur.fetchone()
        
        if user_bus:
            session['bus_number'] = user_bus[0]
        
        return render_template('notification.html')
    except Exception as e:
        print(f"Error in notification route: {str(e)}")
        flash('An error occurred while loading notifications', 'error')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()

@app.route('/test-db')
@require_diagnostics_token
def test_db():
    """Test database connection and show diagnostic information"""
    diagnostic_info = {
        'status': 'unknown',
        'message': '',
        'config': {
            'host': app.config.get('MYSQL_HOST', 'not set'),
            'user': app.config.get('MYSQL_USER', 'not set'),
            'database': app.config.get('MYSQL_DB', 'not set'),
            'port': app.config.get('MYSQL_PORT', 'not set'),
            'password_set': 'Yes' if app.config.get('MYSQL_PASSWORD') else 'No'
        },
        'environment': {
            'is_render': bool(os.getenv('RENDER')),
            'is_vercel': bool(os.getenv('VERCEL')),
            'flask_env': os.getenv('FLASK_ENV', 'not set')
        },
        'error_details': None
    }
    
    try:
        # Try to get connection
        cur = mysql.connection.cursor()
        cur.execute('SELECT 1 as test, DATABASE() as current_db, USER() as current_user, VERSION() as mysql_version')
        result = cur.fetchone()
        cur.close()
        
        diagnostic_info['status'] = 'success'
        diagnostic_info['message'] = 'Database connection successful'
        diagnostic_info['connection_info'] = {
            'test_query': result[0] if result else None,
            'current_database': result[1] if result and len(result) > 1 else None,
            'current_user': result[2] if result and len(result) > 2 else None,
            'mysql_version': result[3] if result and len(result) > 3 else None
        }
        
        return jsonify(diagnostic_info)
    except ConnectionError as e:
        diagnostic_info['status'] = 'connection_error'
        diagnostic_info['message'] = str(e)
        diagnostic_info['error_details'] = {
            'type': 'ConnectionError',
            'suggestion': 'Check if MYSQL_HOST environment variable is set correctly'
        }
        return jsonify(diagnostic_info), 500
    except PyMySQLError as e:
        diagnostic_info['status'] = 'database_error'
        diagnostic_info['message'] = str(e)
        error_code = getattr(e, 'args', [None])[0] if hasattr(e, 'args') and e.args else None
        diagnostic_info['error_details'] = {
            'type': 'PyMySQLError',
            'error_code': error_code,
            'suggestion': 'Check database credentials, host, port, and network connectivity'
        }
        return jsonify(diagnostic_info), 500
    except Exception as e:
        diagnostic_info['status'] = 'error'
        diagnostic_info['message'] = str(e)
        diagnostic_info['error_details'] = {
            'type': type(e).__name__,
            'suggestion': 'Check application logs for more details'
        }
        return jsonify(diagnostic_info), 500

@app.route('/db-config')
@require_diagnostics_token
def db_config():
    """Show database configuration (without sensitive data) - for debugging"""
    config_info = {
        'host': app.config.get('MYSQL_HOST', 'not set'),
        'user': app.config.get('MYSQL_USER', 'not set'),
        'database': app.config.get('MYSQL_DB', 'not set'),
        'port': app.config.get('MYSQL_PORT', 'not set'),
        'password_configured': bool(app.config.get('MYSQL_PASSWORD')),
        'environment_variables': {
            'MYSQL_HOST': 'set' if os.getenv('MYSQL_HOST') else 'not set',
            'MYSQL_USER': 'set' if os.getenv('MYSQL_USER') else 'not set',
            'MYSQL_PASSWORD': 'set' if os.getenv('MYSQL_PASSWORD') else 'not set',
            'MYSQL_DB': 'set' if os.getenv('MYSQL_DB') else 'not set',
            'MYSQL_PORT': 'set' if os.getenv('MYSQL_PORT') else 'not set',
            'DB_HOST': 'set' if os.getenv('DB_HOST') else 'not set',
            'DB_USER': 'set' if os.getenv('DB_USER') else 'not set',
            'DB_PASSWORD': 'set' if os.getenv('DB_PASSWORD') else 'not set',
            'DB_NAME': 'set' if os.getenv('DB_NAME') else 'not set',
        },
        'deployment_platform': {
            'is_render': bool(os.getenv('RENDER')),
            'is_vercel': bool(os.getenv('VERCEL')),
        }
    }
    return jsonify(config_info)

@app.route('/view-db')
@require_debug_mode
def view_db():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        # Get user table contents
        cur.execute('SELECT * FROM user')
        users = cur.fetchall()
        
        # Get transactions table contents
        cur.execute('SELECT * FROM transactions')
        transactions = cur.fetchall()
        
        # Get bus table contents
        cur.execute('SELECT * FROM bus')
        buses = cur.fetchall()
        
        return render_template('view_db.html', 
                             users=users, 
                             transactions=transactions, 
                             buses=buses)
    except Exception as e:
        print(f"Error viewing database: {str(e)}")
        flash('Error viewing database', 'error')
        return redirect(url_for('dashboard'))
    finally:
        cur.close()

@app.route('/print-db')
@require_debug_mode
def print_db():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    cur = mysql.connection.cursor()
    try:
        print("\n=== USERS TABLE ===")
        cur.execute('SELECT * FROM user')
        users = cur.fetchall()
        for user in users:
            print(f"ID: {user[0]}, USN: {user[1]}, Name: {user[2]}, Phone: {user[3]}, Email: {user[4]}, Bus: {user[6]}, Balance: {user[8]}")
        
        print("\n=== TRANSACTIONS TABLE ===")
        cur.execute('SELECT * FROM transactions')
        transactions = cur.fetchall()
        for trans in transactions:
            print(f"ID: {trans[0]}, User: {trans[1]}, Amount: {trans[2]}, Type: {trans[3]}, Desc: {trans[4]}, Bus: {trans[5]}, Location: {trans[6]}, Date: {trans[7]}")
        
        print("\n=== BUS TABLE ===")
        cur.execute('SELECT * FROM bus')
        buses = cur.fetchall()
        for bus in buses:
            print(f"Bus: {bus[0]}, From: {bus[1]}, To: {bus[2]}, Total Seats: {bus[3]}, Available: {bus[4]}, Fare: {bus[5]}")
        
        return "Database contents printed to console. Check your terminal."
    except Exception as e:
        print(f"Error printing database: {str(e)}")
        return f"Error: {str(e)}"
    finally:
        cur.close()

@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        feedback_type = request.form['feedback_type']
        rating = int(request.form['rating'])
        feedback_text = request.form['feedback_text']
        
        cur = mysql.connection.cursor()
        cur.execute('''
            INSERT INTO feedback (user_id, feedback_type, rating, feedback_text)
            VALUES (%s, %s, %s, %s)
        ''', (session['user_id'], feedback_type, rating, feedback_text))
        
        mysql.connection.commit()
        cur.close()
        
        flash('Thank you for your feedback!', 'success')
    except Exception as e:
        print(f"Error submitting feedback: {str(e)}")
        flash('Error submitting feedback. Please try again.', 'error')
    
    return redirect(url_for('dashboard'))

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle internal server errors gracefully"""
    print(f"Internal server error: {str(error)}")
    return jsonify({'error': 'Internal server error', 'message': 'An unexpected error occurred'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler to prevent FUNCTION_INVOCATION_FAILED"""
    print(f"Unhandled exception: {str(e)}")
    # Return a proper response instead of letting the exception propagate
    # This prevents FUNCTION_INVOCATION_FAILED errors on Vercel
    try:
        if request.is_json or request.path.startswith('/api/'):
            message = "An unexpected error occurred."
            if not is_production_env():
                message = str(e)
            return jsonify({'error': 'An error occurred', 'message': message}), 500
        flash('An error occurred. Please try again.', 'error')
        return redirect(url_for('index'))
    except Exception:
        # Fallback if even error handling fails
        return jsonify({'error': 'Internal server error'}), 500

# Export handler for Vercel serverless functions
# Vercel Python runtime expects a WSGI application
# The handler must be the Flask app instance
# For Render, gunicorn will use: gunicorn app:app
handler = app

# For local development
if __name__ == '__main__':
    app.run(debug=True) 
