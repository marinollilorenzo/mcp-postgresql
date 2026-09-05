import os
import random
import datetime
from decimal import Decimal
from dotenv import load_dotenv
from faker import Faker
import psycopg2
from psycopg2.extras import execute_values

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "testdb")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")

fake = Faker('it_IT')

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def main():
    print("Connessione al database...")
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Pulisci tabelle e resetta sequence
        print("Pulizia tabelle esistenti...")
        cur.execute("""
            TRUNCATE TABLE inventory_log, reviews, support_tickets, invoices, order_items, orders, 
                           products, suppliers, categories, customers, employees, departments, warehouses
            RESTART IDENTITY CASCADE;
        """)

        print("Generazione Departments...")
        departments = [
            ('Engineering', 850000, 'Milano'),
            ('Sales', 320000, 'Roma'),
            ('Human Resources', 180000, 'Milano'),
            ('Marketing', 250000, 'Torino'),
            ('Customer Support', 140000, 'Napoli')
        ]
        execute_values(cur, "INSERT INTO departments (name, budget, location) VALUES %s", departments)
        
        print("Generazione Warehouses...")
        warehouses = [
            ('Magazzino Nord', 'Milano', 10000, True),
            ('Magazzino Centro', 'Roma', 8000, True),
            ('Magazzino Sud', 'Napoli', 5000, True)
        ]
        execute_values(cur, "INSERT INTO warehouses (name, location, capacity, is_active) VALUES %s", warehouses)

        print("Generazione Employees...")
        roles = ['manager','analyst','developer','sales','support','hr']
        employees = []
        for i in range(50):
            role = random.choice(roles)
            dept_id = random.randint(1, 5)
            # Manager_id is None for first 5, then random manager
            manager_id = random.randint(1, 5) if i >= 5 else None
            employees.append((
                fake.name(),
                fake.unique.email(),
                role,
                random.randint(30000, 100000),
                dept_id,
                manager_id,
                fake.date_between(start_date='-5y', end_date='today')
            ))
        execute_values(cur, """
            INSERT INTO employees (full_name, email, role, salary, department_id, manager_id, hire_date) 
            VALUES %s
        """, employees)

        print("Generazione Customers (5000)...")
        customers = []
        for _ in range(5000):
            customers.append((
                fake.name(),
                fake.unique.email(),
                fake.phone_number()[:20],
                fake.city(),
                'Italy',
                fake.date_time_between(start_date='-3y', end_date='now')
            ))
        execute_values(cur, """
            INSERT INTO customers (full_name, email, phone, city, country, registered_at) 
            VALUES %s
        """, customers)

        print("Generazione Categories...")
        categories = [
            ('Elettronica', 'elettronica', None),
            ('Abbigliamento', 'abbigliamento', None),
            ('Casa & Giardino', 'casa-giardino', None),
            ('Sport', 'sport', None),
            ('Libri', 'libri', None),
            ('Smartphone', 'smartphone', 1),
            ('Laptop', 'laptop', 1),
            ('Uomo', 'abbigliamento-uomo', 2),
            ('Donna', 'abbigliamento-donna', 2),
        ]
        execute_values(cur, "INSERT INTO categories (name, slug, parent_id) VALUES %s", categories)

        print("Generazione Suppliers...")
        suppliers = []
        for _ in range(20):
            suppliers.append((
                fake.company(),
                fake.unique.company_email(),
                fake.country(),
                round(random.uniform(3.0, 5.0), 2)
            ))
        execute_values(cur, "INSERT INTO suppliers (company_name, contact_email, country, rating) VALUES %s", suppliers)

        print("Generazione Products (500)...")
        products = []
        for i in range(1, 501):
            cost_price = round(random.uniform(5.0, 500.0), 2)
            price = round(cost_price * random.uniform(1.2, 2.5), 2)
            products.append((
                f"{fake.word().capitalize()} {fake.word().capitalize()} {i}",
                fake.unique.ean(length=13),
                fake.text(max_nb_chars=200),
                price,
                cost_price,
                random.randint(10, 500),
                random.randint(6, 9), # use subcategories
                random.randint(1, 20),
                random.randint(1, 3)
            ))
        execute_values(cur, """
            INSERT INTO products (name, sku, description, price, cost_price, stock_qty, category_id, supplier_id, warehouse_id) 
            VALUES %s
        """, products)

        print("Generazione Orders (10000)...")
        order_statuses = ['pending','confirmed','shipped','delivered','cancelled','refunded']
        payment_methods = ['credit_card','paypal','bank_transfer','crypto']
        orders = []
        for _ in range(10000):
            status = random.choices(order_statuses, weights=[10, 10, 20, 50, 5, 5])[0]
            created = fake.date_time_between(start_date='-2y', end_date='now')
            shipped = created + datetime.timedelta(days=random.randint(1,3)) if status in ['shipped', 'delivered'] else None
            delivered = shipped + datetime.timedelta(days=random.randint(1,5)) if status == 'delivered' else None
            
            orders.append((
                random.randint(1, 5000),
                random.randint(1, 50),
                status,
                random.choice(payment_methods),
                0, 0, 0, 0, # amounts will be updated
                created, shipped, delivered
            ))
        execute_values(cur, """
            INSERT INTO orders (customer_id, employee_id, status, payment_method, subtotal, discount, shipping_cost, total_amount, created_at, shipped_at, delivered_at) 
            VALUES %s
        """, orders)

        print("Generazione Order Items...")
        cur.execute("SELECT id, price FROM products")
        product_prices = dict(cur.fetchall())
        
        order_items = []
        for order_id in range(1, 10001):
            num_items = random.randint(1, 5)
            chosen_products = random.sample(list(product_prices.keys()), num_items)
            for p_id in chosen_products:
                qty = random.randint(1, 3)
                unit_price = product_prices[p_id]
                discount = random.choice([0, 0, 0, 5, 10, 20])
                order_items.append((
                    order_id, p_id, qty, unit_price, discount
                ))
        execute_values(cur, """
            INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_pct) 
            VALUES %s
        """, order_items)

        print("Aggiornamento Totali Ordini...")
        cur.execute("""
            UPDATE orders o
            SET subtotal = (SELECT COALESCE(SUM(line_total), 0) FROM order_items oi WHERE oi.order_id = o.id),
                shipping_cost = CASE WHEN (SELECT COALESCE(SUM(line_total), 0) FROM order_items oi WHERE oi.order_id = o.id) > 100 THEN 0 ELSE 5.90 END
            WHERE o.id > 0;
            
            UPDATE orders SET total_amount = subtotal + shipping_cost - discount WHERE id > 0;
        """)

        print("Generazione Invoices...")
        cur.execute("SELECT id, total_amount, created_at FROM orders WHERE status NOT IN ('cancelled')")
        valid_orders = cur.fetchall()
        invoices = []
        for o_id, amount, created in valid_orders:
            invoices.append((
                o_id,
                fake.unique.bothify(text='INV-202?-#####'),
                created,
                created + datetime.timedelta(days=30),
                amount,
                round(float(amount) * 0.22, 2), # 22% VAT
                random.random() > 0.1 # 90% paid
            ))
        execute_values(cur, """
            INSERT INTO invoices (order_id, invoice_number, issued_at, due_date, amount, tax_amount, is_paid) 
            VALUES %s
        """, invoices)

        print("Generazione Support Tickets (2000)...")
        statuses = ['open','in_progress','resolved','closed']
        priorities = ['low','medium','high','urgent']
        tickets = []
        for _ in range(2000):
            status = random.choice(statuses)
            created = fake.date_time_between(start_date='-1y', end_date='now')
            tickets.append((
                random.randint(1, 5000),
                random.randint(1, 50) if status != 'open' else None,
                random.randint(1, 10000) if random.random() > 0.5 else None,
                fake.sentence(),
                fake.paragraph(),
                status,
                random.choice(priorities),
                created,
                created + datetime.timedelta(days=random.randint(1,10)) if status in ['resolved', 'closed'] else None
            ))
        execute_values(cur, """
            INSERT INTO support_tickets (customer_id, employee_id, order_id, subject, description, status, priority, created_at, resolved_at) 
            VALUES %s
        """, tickets)

        print("Aggiornamento metriche Customer...")
        cur.execute("""
            UPDATE customers SET
                total_orders = (SELECT COUNT(*) FROM orders o WHERE o.customer_id = customers.id AND o.status NOT IN ('cancelled')),
                lifetime_value = (SELECT COALESCE(SUM(total_amount), 0) FROM orders o WHERE o.customer_id = customers.id AND o.status NOT IN ('cancelled','refunded'))
            WHERE id > 0;
        """)

        conn.commit()
        print("Generazione completata con successo!")

    except Exception as e:
        conn.rollback()
        print(f"Errore durante la generazione: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    main()
