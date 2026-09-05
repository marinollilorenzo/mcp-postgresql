-- ═══════════════════════════════════════════════════════════════
--  TEST DATABASE: E-Commerce + HR System + CRM + ERP
--  Tabelle: 13 tabelle con FK complesse, ENUM, indici, viste
-- ═══════════════════════════════════════════════════════════════

-- Pulisce tutto se già esiste
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

-- ──────────────────────────────────────────────
--  ENUMS
-- ──────────────────────────────────────────────

CREATE TYPE order_status    AS ENUM ('pending','confirmed','shipped','delivered','cancelled','refunded');
CREATE TYPE payment_method  AS ENUM ('credit_card','paypal','bank_transfer','crypto');
CREATE TYPE employee_role   AS ENUM ('manager','analyst','developer','sales','support','hr');
CREATE TYPE review_sentiment AS ENUM ('positive','neutral','negative');
CREATE TYPE ticket_status   AS ENUM ('open','in_progress','resolved','closed');
CREATE TYPE ticket_priority AS ENUM ('low','medium','high','urgent');

-- ──────────────────────────────────────────────
--  1. DEPARTMENTS (HR)
-- ──────────────────────────────────────────────

CREATE TABLE departments (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    budget      NUMERIC(12,2) NOT NULL DEFAULT 0,
    location    VARCHAR(100),
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────
--  2. EMPLOYEES (self-referencing FK per manager)
-- ──────────────────────────────────────────────

CREATE TABLE employees (
    id              SERIAL PRIMARY KEY,
    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(200) NOT NULL UNIQUE,
    role            employee_role NOT NULL,
    salary          NUMERIC(10,2) NOT NULL,
    department_id   INT REFERENCES departments(id) ON DELETE SET NULL,
    manager_id      INT REFERENCES employees(id) ON DELETE SET NULL,  -- self-ref
    hire_date       DATE NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ──────────────────────────────────────────────
--  3. CUSTOMERS
-- ──────────────────────────────────────────────

CREATE TABLE customers (
    id              SERIAL PRIMARY KEY,
    full_name       VARCHAR(150) NOT NULL,
    email           VARCHAR(200) NOT NULL UNIQUE,
    phone           VARCHAR(20),
    city            VARCHAR(100),
    country         VARCHAR(100) NOT NULL DEFAULT 'Italy',
    total_orders    INT NOT NULL DEFAULT 0,
    lifetime_value  NUMERIC(12,2) NOT NULL DEFAULT 0,
    registered_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login      TIMESTAMP
);

-- ──────────────────────────────────────────────
--  4. CATEGORIES (self-referencing per subcategorie)
-- ──────────────────────────────────────────────

CREATE TABLE categories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    slug        VARCHAR(100) NOT NULL UNIQUE,
    parent_id   INT REFERENCES categories(id) ON DELETE SET NULL,  -- self-ref
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

-- ──────────────────────────────────────────────
--  5. SUPPLIERS
-- ──────────────────────────────────────────────

CREATE TABLE suppliers (
    id              SERIAL PRIMARY KEY,
    company_name    VARCHAR(200) NOT NULL,
    contact_email   VARCHAR(200),
    country         VARCHAR(100),
    rating          NUMERIC(3,2) CHECK (rating BETWEEN 0 AND 5),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- ──────────────────────────────────────────────
--  6. WAREHOUSES
-- ──────────────────────────────────────────────

CREATE TABLE warehouses (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    location    VARCHAR(200) NOT NULL,
    capacity    INT NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

-- ──────────────────────────────────────────────
--  7. PRODUCTS
-- ──────────────────────────────────────────────

CREATE TABLE products (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    sku             VARCHAR(50) NOT NULL UNIQUE,
    description     TEXT,
    price           NUMERIC(10,2) NOT NULL CHECK (price >= 0),
    cost_price      NUMERIC(10,2) NOT NULL CHECK (cost_price >= 0),
    stock_qty       INT NOT NULL DEFAULT 0,
    category_id     INT REFERENCES categories(id) ON DELETE SET NULL,
    supplier_id     INT REFERENCES suppliers(id) ON DELETE SET NULL,
    warehouse_id    INT REFERENCES warehouses(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_supplier ON products(supplier_id);
CREATE INDEX idx_products_warehouse ON products(warehouse_id);
CREATE INDEX idx_products_price    ON products(price);

-- ──────────────────────────────────────────────
--  8. ORDERS
-- ──────────────────────────────────────────────

CREATE TABLE orders (
    id              SERIAL PRIMARY KEY,
    customer_id     INT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    employee_id     INT REFERENCES employees(id) ON DELETE SET NULL,  -- sales rep
    status          order_status NOT NULL DEFAULT 'pending',
    payment_method  payment_method NOT NULL,
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0,
    discount        NUMERIC(12,2) NOT NULL DEFAULT 0,
    shipping_cost   NUMERIC(8,2) NOT NULL DEFAULT 0,
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    shipped_at      TIMESTAMP,
    delivered_at    TIMESTAMP
);

CREATE INDEX idx_orders_customer   ON orders(customer_id);
CREATE INDEX idx_orders_status     ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);

-- ──────────────────────────────────────────────
--  9. ORDER_ITEMS (tabella ponte orders ↔ products)
-- ──────────────────────────────────────────────

CREATE TABLE order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity    INT NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2) NOT NULL,
    discount_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    line_total  NUMERIC(12,2) GENERATED ALWAYS AS
                    (quantity * unit_price * (1 - discount_pct / 100)) STORED,
    UNIQUE (order_id, product_id)
);

CREATE INDEX idx_order_items_order   ON order_items(order_id);
CREATE INDEX idx_order_items_product ON order_items(product_id);

-- ──────────────────────────────────────────────
--  10. INVOICES
-- ──────────────────────────────────────────────

CREATE TABLE invoices (
    id              SERIAL PRIMARY KEY,
    order_id        INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    invoice_number  VARCHAR(50) NOT NULL UNIQUE,
    issued_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    due_date        DATE NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    tax_amount      NUMERIC(12,2) NOT NULL DEFAULT 0,
    is_paid         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_invoices_order ON invoices(order_id);

-- ──────────────────────────────────────────────
--  11. SUPPORT_TICKETS
-- ──────────────────────────────────────────────

CREATE TABLE support_tickets (
    id              SERIAL PRIMARY KEY,
    customer_id     INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    employee_id     INT REFERENCES employees(id) ON DELETE SET NULL,
    order_id        INT REFERENCES orders(id) ON DELETE SET NULL,
    subject         VARCHAR(255) NOT NULL,
    description     TEXT NOT NULL,
    status          ticket_status NOT NULL DEFAULT 'open',
    priority        ticket_priority NOT NULL DEFAULT 'medium',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMP
);

CREATE INDEX idx_tickets_customer ON support_tickets(customer_id);
CREATE INDEX idx_tickets_status ON support_tickets(status);

-- ──────────────────────────────────────────────
--  12. REVIEWS
-- ──────────────────────────────────────────────

CREATE TABLE reviews (
    id              SERIAL PRIMARY KEY,
    product_id      INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    customer_id     INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    sentiment       review_sentiment,
    title           VARCHAR(200),
    body            TEXT,
    is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, customer_id)  -- un cliente, una recensione per prodotto
);

CREATE INDEX idx_reviews_product  ON reviews(product_id);
CREATE INDEX idx_reviews_rating   ON reviews(rating);

-- ──────────────────────────────────────────────
--  13. INVENTORY_LOG (traccia movimenti stock)
-- ──────────────────────────────────────────────

CREATE TABLE inventory_log (
    id              SERIAL PRIMARY KEY,
    product_id      INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    warehouse_id    INT REFERENCES warehouses(id) ON DELETE SET NULL,
    change_qty      INT NOT NULL,          -- positivo=entrata, negativo=uscita
    reason          VARCHAR(100) NOT NULL, -- 'purchase','return','damage','manual'
    order_id        INT REFERENCES orders(id) ON DELETE SET NULL,
    employee_id     INT REFERENCES employees(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_inventory_product ON inventory_log(product_id);

-- ──────────────────────────────────────────────
--  VIEWS UTILI
-- ──────────────────────────────────────────────

-- Vista: fatturato mensile
CREATE VIEW monthly_revenue AS
SELECT
    DATE_TRUNC('month', created_at)::DATE AS month,
    COUNT(*)                              AS order_count,
    SUM(total_amount)                     AS revenue,
    AVG(total_amount)                     AS avg_order_value
FROM orders
WHERE status NOT IN ('cancelled','refunded')
GROUP BY 1
ORDER BY 1;

-- Vista: prodotti con stats recensioni
CREATE VIEW product_stats AS
SELECT
    p.id,
    p.name,
    p.price,
    p.stock_qty,
    COALESCE(AVG(r.rating), 0)::NUMERIC(3,2) AS avg_rating,
    COUNT(r.id)                              AS review_count,
    SUM(oi.quantity)                         AS total_sold
FROM products p
LEFT JOIN reviews r     ON r.product_id = p.id
LEFT JOIN order_items oi ON oi.product_id = p.id
         JOIN orders o ON o.id = oi.order_id AND o.status = 'delivered'
GROUP BY p.id, p.name, p.price, p.stock_qty;

-- NOTA: I dati mock ora vengono generati e inseriti dinamicamente via script Python (generate_faker_data.py)
