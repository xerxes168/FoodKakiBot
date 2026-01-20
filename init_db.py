import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    database="postgres",
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", "password")
)
conn.autocommit = True
cur = conn.cursor()

# Create database
try:
    cur.execute("CREATE DATABASE restaurant_chatbot")
    print("Database created successfully")
except psycopg2.errors.DuplicateDatabase:
    print("Database already exists")

cur.close()
conn.close()

# Connect to the new database and create tables
conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "localhost"),
    database="restaurant_chatbot",
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", "password")
)
cur = conn.cursor()

# Create tables
cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        session_id UUID REFERENCES sessions(id),
        role VARCHAR(20) NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS user_preferences (
        id SERIAL PRIMARY KEY,
        session_id UUID REFERENCES sessions(id),
        location VARCHAR(255),
        dietary_restrictions TEXT[],
        budget_range VARCHAR(50),
        favorite_cuisines TEXT[]
    )
""")

cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")

conn.commit()
cur.close()
conn.close()

print("Tables created successfully")
