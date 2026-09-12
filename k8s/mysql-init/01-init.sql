CREATE DATABASE IF NOT EXISTS hm_shopping_mart;
USE hm_shopping_mart;

CREATE TABLE roles (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 name VARCHAR(50) NOT NULL UNIQUE,
 description VARCHAR(255),
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE users (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 role_id BIGINT UNSIGNED NOT NULL,
 full_name VARCHAR(150) NOT NULL,
 username VARCHAR(80) UNIQUE,
 email VARCHAR(255) NOT NULL UNIQUE,
 phone VARCHAR(30) UNIQUE,
 password_hash VARCHAR(255) NOT NULL,
 is_active BOOLEAN DEFAULT TRUE,
 email_verified BOOLEAN DEFAULT FALSE,
 last_login_at DATETIME NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(role_id) REFERENCES roles(id)
);
CREATE TABLE password_reset_tokens (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 token_hash VARCHAR(255) NOT NULL,
 expires_at DATETIME NOT NULL,
 used_at DATETIME NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE categories (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 parent_id BIGINT UNSIGNED NULL,
 name VARCHAR(150) NOT NULL,
 slug VARCHAR(180) NOT NULL UNIQUE,
 description TEXT,
 image_url VARCHAR(500),
 is_active BOOLEAN DEFAULT TRUE,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(parent_id) REFERENCES categories(id) ON DELETE SET NULL
);
CREATE TABLE brands (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 name VARCHAR(150) NOT NULL UNIQUE,
 slug VARCHAR(180) NOT NULL UNIQUE,
 logo_url VARCHAR(500),
 is_active BOOLEAN DEFAULT TRUE,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sellers (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL UNIQUE,
 store_name VARCHAR(200) NOT NULL,
 gst_number VARCHAR(50),
 status ENUM('pending','approved','suspended') DEFAULT 'pending',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE products (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 seller_id BIGINT UNSIGNED NULL,
 category_id BIGINT UNSIGNED NOT NULL,
 brand_id BIGINT UNSIGNED NULL,
 name VARCHAR(255) NOT NULL,
 slug VARCHAR(280) NOT NULL UNIQUE,
 sku VARCHAR(100) NOT NULL UNIQUE,
 description TEXT,
 price DECIMAL(12,2) NOT NULL,
 mrp DECIMAL(12,2) NOT NULL,
 discount_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
 stock_quantity INT UNSIGNED DEFAULT 0,
 tax_percent DECIMAL(5,2) DEFAULT 0,
 status ENUM('draft','active','inactive','out_of_stock') DEFAULT 'draft',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(seller_id) REFERENCES sellers(id) ON DELETE SET NULL,
 FOREIGN KEY(category_id) REFERENCES categories(id),
 FOREIGN KEY(brand_id) REFERENCES brands(id) ON DELETE SET NULL
);
CREATE TABLE product_images (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 product_id BIGINT UNSIGNED NOT NULL,
 image_url LONGTEXT NOT NULL,
 sort_order INT DEFAULT 0,
 is_primary BOOLEAN DEFAULT FALSE,
 FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE product_variants (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 product_id BIGINT UNSIGNED NOT NULL,
 variant_name VARCHAR(150) NOT NULL,
 sku VARCHAR(100) NOT NULL UNIQUE,
 price DECIMAL(12,2) NOT NULL,
 stock_quantity INT UNSIGNED DEFAULT 0,
 attributes_json JSON,
 FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE addresses (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 address_type ENUM('home','work','other') DEFAULT 'home',
 recipient_name VARCHAR(150) NOT NULL,
 phone VARCHAR(30) NOT NULL,
 address_line1 VARCHAR(255) NOT NULL,
 address_line2 VARCHAR(255),
 city VARCHAR(100) NOT NULL,
 state VARCHAR(100) NOT NULL,
 postal_code VARCHAR(20) NOT NULL,
 country VARCHAR(100) DEFAULT 'India',
 is_default BOOLEAN DEFAULT FALSE,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE carts (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL UNIQUE,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE cart_items (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 cart_id BIGINT UNSIGNED NOT NULL,
 product_id BIGINT UNSIGNED NOT NULL,
 variant_id BIGINT UNSIGNED NULL,
 quantity INT UNSIGNED NOT NULL,
 unit_price DECIMAL(12,2) NOT NULL,
 FOREIGN KEY(cart_id) REFERENCES carts(id) ON DELETE CASCADE,
 FOREIGN KEY(product_id) REFERENCES products(id),
 FOREIGN KEY(variant_id) REFERENCES product_variants(id) ON DELETE SET NULL
);
CREATE TABLE wishlists (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 product_id BIGINT UNSIGNED NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
 UNIQUE(user_id,product_id)
);
CREATE TABLE coupons (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(80) NOT NULL UNIQUE,
 description VARCHAR(255),
 discount_type ENUM('percent','fixed') NOT NULL,
 discount_value DECIMAL(12,2) NOT NULL,
 minimum_order_amount DECIMAL(12,2) DEFAULT 0,
 maximum_discount DECIMAL(12,2) NULL,
 starts_at DATETIME NOT NULL,
 expires_at DATETIME NOT NULL,
 usage_limit INT UNSIGNED NULL,
 used_count INT UNSIGNED DEFAULT 0,
 is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE orders (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 address_id BIGINT UNSIGNED NOT NULL,
 coupon_id BIGINT UNSIGNED NULL,
 order_number VARCHAR(50) NOT NULL UNIQUE,
 subtotal DECIMAL(12,2) NOT NULL,
 discount_amount DECIMAL(12,2) DEFAULT 0,
 tax_amount DECIMAL(12,2) DEFAULT 0,
 shipping_amount DECIMAL(12,2) DEFAULT 0,
 total_amount DECIMAL(12,2) NOT NULL,
 status ENUM('pending','confirmed','packed','shipped','delivered','cancelled','returned') DEFAULT 'pending',
 payment_status ENUM('pending','paid','failed','refunded') DEFAULT 'pending',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id),
 FOREIGN KEY(address_id) REFERENCES addresses(id),
 FOREIGN KEY(coupon_id) REFERENCES coupons(id) ON DELETE SET NULL
);
CREATE TABLE order_items (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 order_id BIGINT UNSIGNED NOT NULL,
 product_id BIGINT UNSIGNED NOT NULL,
 seller_id BIGINT UNSIGNED NULL,
 product_name VARCHAR(255) NOT NULL,
 sku VARCHAR(100) NOT NULL,
 quantity INT UNSIGNED NOT NULL,
 unit_price DECIMAL(12,2) NOT NULL,
 tax_amount DECIMAL(12,2) DEFAULT 0,
 total_amount DECIMAL(12,2) NOT NULL,
 FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
 FOREIGN KEY(product_id) REFERENCES products(id),
 FOREIGN KEY(seller_id) REFERENCES sellers(id) ON DELETE SET NULL
);
CREATE TABLE payments (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 order_id BIGINT UNSIGNED NOT NULL,
 transaction_id VARCHAR(150) UNIQUE,
 provider VARCHAR(80),
 amount DECIMAL(12,2) NOT NULL,
 currency VARCHAR(10) DEFAULT 'INR',
 status ENUM('initiated','success','failed','refunded') DEFAULT 'initiated',
 paid_at DATETIME NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(order_id) REFERENCES orders(id)
);
CREATE TABLE shipments (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 order_id BIGINT UNSIGNED NOT NULL UNIQUE,
 carrier VARCHAR(100),
 tracking_number VARCHAR(150),
 current_city VARCHAR(100),
 current_location VARCHAR(255),
 estimated_delivery DATETIME NULL,
 status ENUM('pending','packed','shipped','out_for_delivery','delivered','returned') DEFAULT 'pending',
 shipped_at DATETIME NULL,
 delivered_at DATETIME NULL,
 FOREIGN KEY(order_id) REFERENCES orders(id)
);
CREATE TABLE reviews (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 product_id BIGINT UNSIGNED NOT NULL,
 order_item_id BIGINT UNSIGNED NULL,
 rating TINYINT UNSIGNED NOT NULL,
 title VARCHAR(200),
 review_text TEXT,
 status ENUM('pending','approved','rejected') DEFAULT 'pending',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id),
 FOREIGN KEY(product_id) REFERENCES products(id),
 FOREIGN KEY(order_item_id) REFERENCES order_items(id) ON DELETE SET NULL
);
CREATE TABLE notifications (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 title VARCHAR(200) NOT NULL,
 message TEXT NOT NULL,
 type VARCHAR(50) DEFAULT 'general',
 is_read BOOLEAN DEFAULT FALSE,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE audit_logs (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NULL,
 action VARCHAR(100) NOT NULL,
 entity_type VARCHAR(100),
 entity_id BIGINT UNSIGNED NULL,
 ip_address VARCHAR(64),
 details JSON,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE banners (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 title VARCHAR(200) NOT NULL,
 image_url LONGTEXT NOT NULL,
 link_url VARCHAR(500),
 sort_order INT DEFAULT 0,
 is_active BOOLEAN DEFAULT TRUE,
 starts_at DATETIME NULL,
 ends_at DATETIME NULL
);
CREATE TABLE payments_refunds (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 payment_id BIGINT UNSIGNED NOT NULL,
 refund_transaction_id VARCHAR(150) UNIQUE,
 amount DECIMAL(12,2) NOT NULL,
 reason VARCHAR(255),
 status ENUM('requested','processed','failed') DEFAULT 'requested',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(payment_id) REFERENCES payments(id)
);
CREATE TABLE return_requests (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 order_id BIGINT UNSIGNED NOT NULL,
 order_item_id BIGINT UNSIGNED NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 admin_id BIGINT UNSIGNED NULL,
 quantity INT UNSIGNED NOT NULL,
 reason VARCHAR(500) NOT NULL,
 status ENUM('requested','approved','rejected','received','refunded','cancelled') DEFAULT 'requested',
 admin_note VARCHAR(1000),
 refund_amount DECIMAL(12,2) DEFAULT 0,
 requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 processed_at DATETIME NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
 FOREIGN KEY(order_item_id) REFERENCES order_items(id) ON DELETE CASCADE,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
 FOREIGN KEY(admin_id) REFERENCES users(id) ON DELETE SET NULL,
 INDEX idx_return_user (user_id),
 INDEX idx_return_order (order_id),
 INDEX idx_return_item (order_item_id)
);
CREATE TABLE audit_events (
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
);

CREATE TABLE support_tickets (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT UNSIGNED NOT NULL,
 subject VARCHAR(255) NOT NULL,
 message TEXT NOT NULL,
 status ENUM('open','in_progress','resolved','closed') DEFAULT 'open',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO roles(name,description) VALUES
('customer','Normal customer who can purchase'),
('admin','Administrator; cannot purchase'),
('seller','Marketplace seller');

INSERT INTO categories(name,slug) VALUES
('Mobiles & Tablets','mobiles-tablets'),('Laptops & Computers','laptops-computers'),('TVs & Appliances','tvs-appliances'),('Audio','audio'),('Cameras & Accessories','cameras-accessories'),('Electronics','electronics'),('Men Fashion','men-fashion'),('Women Fashion','women-fashion'),('Kids Fashion','kids-fashion'),('Shoes & Footwear','shoes-footwear'),('Bags & Luggage','bags-luggage'),('Watches','watches'),('Beauty & Personal Care','beauty-personal-care'),('Home & Kitchen','home-kitchen'),('Furniture','furniture'),('Grocery & Food','grocery-food'),('Sports & Fitness','sports-fitness'),('Books & Stationery','books-stationery'),('Automotive & Car Accessories','automotive-car-accessories'),('Bike & Motorcycle Accessories','bike-motorcycle-accessories'),('Toys & Games','toys-games'),('Jewellery','jewellery'),('Pet Supplies','pet-supplies'),('Tools & Hardware','tools-hardware'),('Travel & Lifestyle','travel-lifestyle');
