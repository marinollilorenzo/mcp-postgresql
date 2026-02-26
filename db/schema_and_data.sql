-- ═══════════════════════════════════════════════════════════════
--  TEST DATABASE: E-Commerce + HR System
--  Tabelle: 10 tabelle con FK complesse, ENUM, indici, viste
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
--  6. PRODUCTS
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
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_supplier ON products(supplier_id);
CREATE INDEX idx_products_price    ON products(price);

-- ──────────────────────────────────────────────
--  7. ORDERS
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
--  8. ORDER_ITEMS (tabella ponte orders ↔ products)
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
--  9. REVIEWS
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
--  10. INVENTORY_LOG (traccia movimenti stock)
-- ──────────────────────────────────────────────

CREATE TABLE inventory_log (
    id              SERIAL PRIMARY KEY,
    product_id      INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
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

-- ═══════════════════════════════════════════════════════════════
--  DATI DI TEST
-- ═══════════════════════════════════════════════════════════════

-- Departments
INSERT INTO departments (name, budget, location) VALUES
    ('Engineering',      850000, 'Milano'),
    ('Sales',            320000, 'Roma'),
    ('Human Resources',  180000, 'Milano'),
    ('Marketing',        250000, 'Torino'),
    ('Customer Support', 140000, 'Napoli');

-- Employees (prima i manager senza manager_id)
INSERT INTO employees (full_name, email, role, salary, department_id, manager_id, hire_date) VALUES
    ('Luca Ferrari',    'l.ferrari@company.it',    'manager',   95000, 1, NULL,  '2018-03-01'),
    ('Sara Bianchi',    's.bianchi@company.it',    'manager',   88000, 2, NULL,  '2019-06-15'),
    ('Marco Verdi',     'm.verdi@company.it',      'hr',        62000, 3, NULL,  '2020-01-10'),
    ('Giulia Romano',   'g.romano@company.it',     'manager',   91000, 4, NULL,  '2017-11-20'),
    ('Andrea Esposito', 'a.esposito@company.it',   'manager',   85000, 5, NULL,  '2019-02-28');

INSERT INTO employees (full_name, email, role, salary, department_id, manager_id, hire_date) VALUES
    ('Chiara Conti',    'c.conti@company.it',      'developer', 72000, 1, 1, '2021-04-05'),
    ('Davide Ricci',    'd.ricci@company.it',       'developer', 68000, 1, 1, '2022-07-18'),
    ('Elena Galli',     'e.galli@company.it',       'analyst',   65000, 1, 1, '2021-09-01'),
    ('Francesco Moro',  'f.moro@company.it',        'sales',     55000, 2, 2, '2022-01-15'),
    ('Valentina Costa', 'v.costa@company.it',       'sales',     52000, 2, 2, '2022-03-20'),
    ('Roberto Sala',    'r.sala@company.it',        'sales',     57000, 2, 2, '2021-08-10'),
    ('Monica Serra',    'm.serra@company.it',       'support',   45000, 5, 5, '2023-02-01'),
    ('Paolo Gentile',   'p.gentile@company.it',     'support',   43000, 5, 5, '2023-05-15'),
    ('Stefania Bruno',  's.bruno@company.it',       'analyst',   61000, 4, 4, '2022-11-01');

-- Categories (con subcategorie)
INSERT INTO categories (name, slug, parent_id) VALUES
    ('Elettronica',       'elettronica',          NULL),
    ('Abbigliamento',     'abbigliamento',         NULL),
    ('Casa & Giardino',   'casa-giardino',         NULL),
    ('Sport',             'sport',                 NULL),
    ('Libri & Media',     'libri-media',           NULL),
    ('Smartphone',        'smartphone',            1),
    ('Laptop',            'laptop',                1),
    ('Audio',             'audio',                 1),
    ('Uomo',              'abbigliamento-uomo',    2),
    ('Donna',             'abbigliamento-donna',   2),
    ('Cucina',            'cucina',                3),
    ('Arredamento',       'arredamento',           3),
    ('Fitness',           'fitness',               4),
    ('Outdoor',           'outdoor',               4),
    ('Romanzi',           'romanzi',               5),
    ('Tecnologia',        'libri-tecnologia',      5);

-- Suppliers
INSERT INTO suppliers (company_name, contact_email, country, rating) VALUES
    ('TechDistrib SRL',     'orders@techdistrib.it',     'Italy',   4.8),
    ('GlobalGoods GmbH',    'supply@globalgoods.de',     'Germany', 4.5),
    ('FashionLine SPA',     'b2b@fashionline.it',        'Italy',   4.2),
    ('SportPro Ltd',        'wholesale@sportpro.co.uk',  'UK',      4.7),
    ('MediaWorld SRL',      'trade@mediaworld.it',       'Italy',   3.9);

-- Products
INSERT INTO products (name, sku, price, cost_price, stock_qty, category_id, supplier_id) VALUES
    ('iPhone 15 Pro 256GB',           'APL-IP15P-256',   1299.00,  900.00,  45,  6, 1),
    ('Samsung Galaxy S24 Ultra',      'SAM-S24U-512',    1199.00,  820.00,  38,  6, 2),
    ('MacBook Pro M3 14"',            'APL-MBP-M3-14',   2199.00, 1600.00,  22,  7, 1),
    ('Dell XPS 15 OLED',              'DEL-XPS15-OLED',  1799.00, 1250.00,  17,  7, 2),
    ('Sony WH-1000XM5',               'SNY-WH1000XM5',    349.00,  210.00,  89,  8, 1),
    ('AirPods Pro 2ª gen',            'APL-APP-GEN2',     279.00,  160.00, 120,  8, 1),
    ('Giacca Pelle Uomo M',           'FSH-GPU-M-BLK',    189.00,   65.00,  34,  9, 3),
    ('Dress Seta Donna L',            'FSH-DSD-L-RED',    229.00,   80.00,  28, 10, 3),
    ('Sneaker Running Uomo 42',       'SPT-SRU-42-WHT',    89.00,   35.00,  76, 13, 4),
    ('Set Pentole Antiaderente 5pz',  'CSA-PNT-5PZ-BLK',  129.00,   48.00,  52, 11, 2),
    ('Robot da Cucina 1200W',         'CSA-RBC-1200W',    399.00,  220.00,  31, 11, 2),
    ('Tappetino Yoga Premium',        'SPT-TYP-6MM-PNK',   39.00,   12.00, 145, 13, 4),
    ('Zaino Trekking 45L',            'SPT-ZTR-45L-GRN',   79.00,   28.00,  63, 14, 4),
    ('Python Avanzato (Libro)',       'LIB-PYT-ADV-IT',    42.00,   15.00, 200, 16, 5),
    ('Manuale del Sysadmin Linux',    'LIB-SYS-LNX-IT',    38.00,   14.00, 180, 16, 5),
    ('Sedia da Ufficio Ergonomica',   'ARR-SUE-ERGO-BLK',  349.00,  160.00,  19, 12, 2),
    ('Monitor LG 27" 4K IPS',        'LGE-MON-27-4K',     589.00,  380.00,  27,  7, 1),
    ('Tastiera Meccanica RGB',        'PRF-KBD-MECH-RGB',  149.00,   62.00,  58,  7, 1),
    ('Mouse Wireless Logitech MX',   'LGT-MX-MSTR-3',     109.00,   52.00,  71,  7, 1),
    ('Romanzo: Il Nome della Rosa',  'LIB-ROM-NR-IT',      16.00,    5.00, 300, 15, 5);

-- Customers
INSERT INTO customers (full_name, email, phone, city, country, registered_at, last_login) VALUES
    ('Mario Rossi',         'mario.rossi@gmail.com',      '+39 333 111 2222', 'Roma',     'Italy',  '2021-03-15', '2024-11-20'),
    ('Anna Verdi',          'anna.verdi@gmail.com',       '+39 347 333 4444', 'Milano',   'Italy',  '2020-08-22', '2024-11-18'),
    ('Giovanni Neri',       'g.neri@libero.it',           '+39 320 555 6666', 'Napoli',   'Italy',  '2022-01-10', '2024-10-30'),
    ('Francesca Blu',       'f.blu@outlook.com',          '+39 339 777 8888', 'Torino',   'Italy',  '2021-11-05', '2024-11-15'),
    ('Luca Marroni',        'luca.m@yahoo.it',            '+39 366 999 0000', 'Firenze',  'Italy',  '2023-02-28', '2024-11-10'),
    ('Elena Grigi',         'elena.g@gmail.com',          '+39 348 121 3141', 'Bologna',  'Italy',  '2020-06-17', '2024-11-22'),
    ('Roberto Arancio',     'r.arancio@gmail.com',        '+39 377 516 1718', 'Palermo',  'Italy',  '2022-09-03', '2024-09-15'),
    ('Silvia Viola',        'silvia.v@outlook.com',       '+39 391 192 0212', 'Venezia',  'Italy',  '2021-04-20', '2024-11-01'),
    ('Matteo Azzurro',      'matteo.az@gmail.com',        '+39 328 223 2425', 'Genova',   'Italy',  '2023-07-12', '2024-10-05'),
    ('Cristina Rosa',       'c.rosa@libero.it',           '+39 360 262 7282', 'Bari',     'Italy',  '2019-12-01', '2024-11-19'),
    ('Alejandro García',    'a.garcia@gmail.com',         '+34 612 345 678',  'Madrid',   'Spain',  '2022-05-14', '2024-11-08'),
    ('Sophie Martin',       's.martin@gmail.com',         '+33 612 345 678',  'Paris',    'France', '2021-08-30', '2024-10-25');

-- Orders (con diversi status e metodi di pagamento)
INSERT INTO orders (customer_id, employee_id, status, payment_method, subtotal, discount, shipping_cost, total_amount, created_at, shipped_at, delivered_at) VALUES
    (1,  9,  'delivered',  'credit_card',   1299.00,   0.00,  0.00, 1299.00, '2024-09-05', '2024-09-06', '2024-09-09'),
    (1,  10, 'delivered',  'paypal',         279.00,   0.00,  5.90,  284.90, '2024-10-12', '2024-10-13', '2024-10-16'),
    (2,  9,  'delivered',  'credit_card',   2199.00, 100.00,  0.00, 2099.00, '2024-08-20', '2024-08-21', '2024-08-25'),
    (2,  11, 'shipped',    'bank_transfer',  349.00,   0.00,  0.00,  349.00, '2024-11-01', '2024-11-02', NULL),
    (3,  9,  'delivered',  'paypal',         189.00,   0.00,  6.90,  195.90, '2024-07-18', '2024-07-19', '2024-07-22'),
    (3,  10, 'cancelled',  'credit_card',   1199.00,   0.00,  0.00, 1199.00, '2024-10-25', NULL,          NULL),
    (4,  11, 'delivered',  'credit_card',    399.00,  20.00,  0.00,  379.00, '2024-06-10', '2024-06-11', '2024-06-14'),
    (4,  9,  'delivered',  'paypal',         349.00,   0.00,  0.00,  349.00, '2024-09-22', '2024-09-23', '2024-09-26'),
    (5,  10, 'confirmed',  'credit_card',    129.00,   0.00,  7.90,  136.90, '2024-11-10', NULL,          NULL),
    (6,  11, 'delivered',  'bank_transfer', 2199.00,   0.00,  0.00, 2199.00, '2024-05-03', '2024-05-04', '2024-05-08'),
    (6,  9,  'delivered',  'credit_card',    589.00,   0.00,  0.00,  589.00, '2024-08-15', '2024-08-16', '2024-08-19'),
    (6,  10, 'delivered',  'paypal',         149.00,   0.00,  0.00,  149.00, '2024-10-02', '2024-10-03', '2024-10-06'),
    (7,  11, 'refunded',   'credit_card',    229.00,   0.00,  6.90,  235.90, '2024-09-10', '2024-09-11', '2024-09-14'),
    (8,  9,  'delivered',  'paypal',          79.00,   0.00,  4.90,   83.90, '2024-10-20', '2024-10-21', '2024-10-24'),
    (8,  10, 'delivered',  'credit_card',     42.00,   0.00,  3.90,   45.90, '2024-11-05', '2024-11-06', '2024-11-08'),
    (9,  11, 'pending',    'paypal',         109.00,   0.00,  0.00,  109.00, '2024-11-15', NULL,          NULL),
    (10, 9,  'delivered',  'credit_card',   1799.00,  50.00,  0.00, 1749.00, '2024-04-12', '2024-04-13', '2024-04-17'),
    (10, 10, 'delivered',  'paypal',         279.00,   0.00,  0.00,  279.00, '2024-07-28', '2024-07-29', '2024-08-01'),
    (10, 11, 'delivered',  'bank_transfer',  399.00,   0.00,  0.00,  399.00, '2024-09-30', '2024-10-01', '2024-10-04'),
    (11, 9,  'delivered',  'credit_card',   1299.00,   0.00,  0.00, 1299.00, '2024-10-08', '2024-10-09', '2024-10-13'),
    (12, 10, 'shipped',    'paypal',         349.00,   0.00,  9.90,  358.90, '2024-11-08', '2024-11-09', NULL);

-- Order Items
INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_pct) VALUES
    (1,  1,  1, 1299.00, 0),
    (2,  6,  1,  279.00, 0),
    (3,  3,  1, 2199.00, 0),
    (3,  18, 1,  149.00, 0), -- MacBook + tastiera nello stesso ordine
    (4,  5,  1,  349.00, 0),
    (5,  7,  1,  189.00, 0),
    (6,  2,  1, 1199.00, 0),
    (7,  11, 1,  399.00, 5),
    (8,  16, 1,  349.00, 0),
    (9,  10, 1,  129.00, 0),
    (10, 3,  1, 2199.00, 0),
    (11, 17, 1,  589.00, 0),
    (12, 18, 1,  149.00, 0),
    (13, 8,  1,  229.00, 0),
    (14, 13, 1,   79.00, 0),
    (15, 14, 1,   42.00, 0),
    (16, 19, 1,  109.00, 0),
    (17, 4,  1, 1799.00, 0),
    (18, 6,  1,  279.00, 0),
    (19, 11, 1,  399.00, 0),
    (20, 1,  1, 1299.00, 0),
    (21, 5,  1,  349.00, 0),
    (3,  19, 1,  109.00, 0),  -- terzo prodotto nello stesso ordine
    (10, 18, 1,  149.00, 0),
    (10, 19, 1,  109.00, 0);

-- Reviews
INSERT INTO reviews (product_id, customer_id, rating, sentiment, title, body, is_verified) VALUES
    (1,  1,  5, 'positive', 'Eccezionale!',           'Il miglior iPhone che abbia mai avuto. Fotocamera strepitosa.', TRUE),
    (3,  2,  5, 'positive', 'Laptop perfetto',         'Prestazioni assurde, durata batteria incredibile.',           TRUE),
    (3,  6,  4, 'positive', 'Ottimo ma caro',          'Molto potente, prezzo alto ma giustificato.',                 TRUE),
    (5,  4,  5, 'positive', 'Cuffie top di gamma',     'Cancellazione del rumore perfetta per il lavoro da remoto.',  TRUE),
    (5,  8,  4, 'positive', 'Comode e precise',        'Ottimo suono, un po scomode dopo 3+ ore.',                   TRUE),
    (6,  1,  4, 'positive', 'Buone ma costose',        'Qualità audio ottima, ma il prezzo è elevato per earbuds.',   TRUE),
    (11, 4,  5, 'positive', 'Robot fantastico',        'Veloce, silenzioso, impasta alla perfezione.',               TRUE),
    (16, 6,  3, 'neutral',  'Discreta per il prezzo',  'Abbastanza comoda, ma la schiena fa ancora male dopo 8 ore.', TRUE),
    (4,  10, 4, 'positive', 'Display magnifico',       'OLED splendido, ottimo per design e video editing.',          TRUE),
    (2,  11, 5, 'positive', 'Il migliore Android',     'Display incredibile, batteria che dura giorni.',              TRUE),
    (13, 8,  5, 'positive', 'Zaino solido e capiente', 'Imbottitura lombare eccellente, ottimo per escursioni.',      TRUE),
    (14, 8,  4, 'positive', 'Libro completo',          'Ottime spiegazioni, esempi pratici. Consigliato.',            TRUE),
    (7,  3,  2, 'negative', 'Deludente',               'La pelle si è sbiadita dopo pochi lavaggi.',                  TRUE),
    (12, 5,  5, 'positive', 'Top per yoga',            'Ottima presa, spessore giusto. Ne ho comprati altri due.',    TRUE);

-- Inventory Log
INSERT INTO inventory_log (product_id, change_qty, reason, order_id, employee_id, created_at) VALUES
    (1,  50, 'purchase', NULL, 6, '2024-08-01'),
    (1,  -1, 'return',   1,    6, '2024-09-09'),
    (3,  30, 'purchase', NULL, 7, '2024-07-15'),
    (3,  -1, 'return',   3,    7, '2024-08-25'),
    (3,  -1, 'return',   10,   7, '2024-05-08'),
    (5,  100,'purchase', NULL, 8, '2024-07-01'),
    (5,  -1, 'return',   4,    8, '2024-11-02'),
    (5,  -1, 'return',   21,   8, '2024-11-09'),
    (11, 40, 'purchase', NULL, 6, '2024-05-20'),
    (11, -1, 'return',   7,    6, '2024-06-14'),
    (11, -1, 'return',   19,   6, '2024-10-04'),
    (7,  50, 'purchase', NULL, 7, '2024-06-01'),
    (7,  -1, 'return',   5,    7, '2024-07-22'),
    (16, 25, 'purchase', NULL, 8, '2024-03-01'),
    (16, -1, 'return',   8,    8, '2024-09-26'),
    (4,  20, 'purchase', NULL, 7, '2024-03-15'),
    (4,  -1, 'return',   17,   7, '2024-04-17'),
    (2,  2,  'damage',   NULL, 6, '2024-10-15');

-- Aggiorna contatori denormalizzati nei customers
UPDATE customers SET
    total_orders   = (SELECT COUNT(*) FROM orders o WHERE o.customer_id = customers.id AND o.status NOT IN ('cancelled')),
    lifetime_value = (SELECT COALESCE(SUM(total_amount), 0) FROM orders o WHERE o.customer_id = customers.id AND o.status NOT IN ('cancelled','refunded'));
