import sqlite3
import os

# Пути к базам
NEW_DB = r'C:\Users\ant89\Projects\brawl-scout-bot1\brawl.db'  # новая (21 юзер)
OLD_DB = r'C:\Users\ant89\Projects\brawl-scout-bot\brawl.db'    # старая (все юзеры)

# Подключаемся к новой базе
conn_new = sqlite3.connect(NEW_DB)
cursor_new = conn_new.cursor()

# Подключаемся к старой базе
conn_old = sqlite3.connect(OLD_DB)
cursor_old = conn_old.cursor()

# 1. Смотрим структуру таблицы users в старой базе
cursor_old.execute("PRAGMA table_info(users);")
columns_old = cursor_old.fetchall()
print("📋 Структура таблицы users в СТАРОЙ базе:")
old_col_names = []
for col in columns_old:
    print(f"   {col[1]} ({col[2]})")
    old_col_names.append(col[1])

# 2. Смотрим структуру таблицы users в новой базе
cursor_new.execute("PRAGMA table_info(users);")
columns_new = cursor_new.fetchall()
print("\n📋 Структура таблицы users в НОВОЙ базе:")
new_col_names = []
for col in columns_new:
    print(f"   {col[1]} ({col[2]})")
    new_col_names.append(col[1])

# 3. Определяем, какие колонки есть в новой базе
print(f"\n🔍 Колонки в новой базе: {new_col_names}")

# 4. Проверяем, есть ли колонка joined_date в новой базе
if 'joined_date' not in new_col_names:
    print("❌ В новой базе нет колонки joined_date!")
    exit()

# 5. Формируем запрос для выборки из старой базы
# В старой базе колонка называется joined_date, а не joined_at
select_query = "SELECT user_id, username, first_name, last_name, joined_date, last_active FROM users;"

# 6. Получаем всех пользователей из старой базы
cursor_old.execute(select_query)
old_users = cursor_old.fetchall()
print(f"\n👥 Старых пользователей: {len(old_users)}")

# 7. Получаем всех пользователей из новой базы (чтобы не добавлять дубли)
cursor_new.execute("SELECT user_id FROM users;")
new_user_ids = {row[0] for row in cursor_new.fetchall()}
print(f"👤 Новых пользователей: {len(new_user_ids)}")

# 8. Добавляем старых пользователей, которых нет в новой базе
added = 0
for user in old_users:
    user_id = user[0]
    if user_id not in new_user_ids:
        cursor_new.execute(
            """INSERT INTO users 
               (user_id, username, first_name, last_name, joined_date, last_active) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            user
        )
        added += 1

conn_new.commit()
print(f"✅ Добавлено {added} новых пользователей из старой базы")

# 9. Проверяем итоговое количество
cursor_new.execute("SELECT COUNT(*) FROM users;")
total = cursor_new.fetchone()[0]
print(f"📊 Всего пользователей в объединённой базе: {total}")

# Закрываем соединения
conn_new.close()
conn_old.close()

print("✅ Объединение завершено!")