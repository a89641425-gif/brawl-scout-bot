import sqlite3

# Подключаемся к локальной базе (241 пользователь)
conn_old = sqlite3.connect('brawl.db')
cursor_old = conn_old.cursor()

# Получаем всех пользователей
cursor_old.execute("SELECT user_id, username, first_name, last_name, joined_date, last_active FROM users")
users = cursor_old.fetchall()
conn_old.close()

# Теперь подключаемся к базе на Railway (если она локально)
conn_new = sqlite3.connect('brawl.db')  # замени на путь к базе на Railway
cursor_new = conn_new.cursor()

# Добавляем пользователей, если их нет
for user in users:
    cursor_new.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, joined_date, last_active)
        VALUES (?, ?, ?, ?, ?, ?)
    """, user)

conn_new.commit()
conn_new.close()

print(f"✅ Добавлено {len(users)} пользователей")