# نصب راهبند روی Ubuntu بدون Docker

این راهنما برای سرور `172.18.1.35` و کاربر `b3-rahband` نوشته شده است. مسیر پروژه:

`/data/projects/rahband`

دو سرویس اجرا می‌شوند:

- `anpr.service`: API، پردازش دوربین‌ها، دیتابیس و NRC
- `rahband-web.service`: رابط وب

Nginx هر دو را از آدرس `http://172.18.1.35` ارائه می‌کند.

## ۱. انتقال پروژه

روی ویندوز، PowerShell را در پوشه پروژه باز کنید و فایل انتقال بسازید. محیط مجازی، وابستگی‌های فرانت و فایل‌های runtime منتقل نمی‌شوند چون روی لینوکس دوباره ساخته می‌شوند:

```powershell
cd "C:\Users\gh.erfanian\Documents\ChatGPT\rahband"
tar --exclude=backend/.venv --exclude=frontend/node_modules --exclude=frontend/dist --exclude=runtime -czf rahband-linux.tar.gz .
scp .\rahband-linux.tar.gz b3-rahband@172.18.1.35:/home/b3-rahband/
ssh b3-rahband@172.18.1.35
```

سپس روی سرور:

```bash
sudo mkdir -p /data/projects/rahband
sudo chown -R b3-rahband:b3-rahband /data/projects/rahband
tar -xzf /home/b3-rahband/rahband-linux.tar.gz -C /data/projects/rahband
```

اگر از قبل پوشه `ANPR` حاوی نسخه قدیمی و مهم دارید، قبل از استخراج از آن نسخه پشتیبان بگیرید. پس از انتقال:

```bash
cd /data/projects/rahband
pwd
ls backend frontend deploy legacy_source
```

## ۲. نصب بسته‌های پایه و ODBC 18

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates build-essential python3 python3-venv python3-dev unixodbc-dev ffmpeg nginx
cd /tmp
curl -sSL -O "https://packages.microsoft.com/config/ubuntu/$(. /etc/os-release && echo "$VERSION_ID")/packages-microsoft-prod.deb"
sudo dpkg -i packages-microsoft-prod.deb
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
odbcinst -q -d
```

خروجی فرمان آخر باید شامل `ODBC Driver 18 for SQL Server` باشد.

## ۳. نصب Node.js 22

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version
npm --version
```

نسخه Node باید حداقل `22.13.0` باشد.

## ۴. ساخت محیط پایتون

```bash
cd /data/projects/rahband/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[hardware,anpr]'
python -m pytest -q
deactivate
```

## ۵. تنظیم فایل محیطی

فایل `.env` فعلی پروژه را نگه دارید و فقط مسیرهای ویندوزی یا آزمایشی را اصلاح کنید:

```bash
cd /data/projects/rahband/backend
nano .env
```

این مقادیر باید در فایل وجود داشته باشند:

```dotenv
RAHBAND_CORS_ORIGINS=http://172.18.1.35
RAHBAND_CAMERA_WORKERS_ENABLED=true
RAHBAND_SHADOW_MODE=true
RAHBAND_MQTT_ENABLED=false
RAHBAND_IMAGE_ROOT=/data/projects/rahband/runtime/images
RAHBAND_OUTBOX_PATH=/data/projects/rahband/runtime/outbox.sqlite3
RAHBAND_OWNER_AUDIT_PATH=/data/projects/rahband/runtime/owner_audit.sqlite3
RAHBAND_AUTH_DATABASE_PATH=/data/projects/rahband/runtime/auth.sqlite3
RAHBAND_AUTH_SESSION_HOURS=12
RAHBAND_AUTH_COOKIE_SECURE=false
RAHBAND_PLATE_MODEL_PATH=/data/projects/rahband/legacy_source/ANPR/best_yolo11n_60_epoch_plate_openvino_model
RAHBAND_CHARACTER_MODEL_PATH=/data/projects/rahband/legacy_source/ANPR/best_s_openvino_model
```

رشته اتصال دیتابیس، رمز دوربین‌ها، JSON دوربین‌ها، NRC و کنترل‌توکن باید همان مقادیر تست‌شده ویندوز باشند. فایل `.env` را در گفتگو یا مخزن Git منتشر نکنید.

```bash
mkdir -p /data/projects/rahband/runtime/images/plates
mkdir -p /data/projects/rahband/runtime/images/vehicles
chmod 600 /data/projects/rahband/backend/.env
```

یک کاربر اولیه بسازید؛ رمز هنگام تایپ در ترمینال نمایش داده نمی‌شود:

```bash
cd /data/projects/rahband/backend
.venv/bin/python scripts/manage_users.py add admin
```

برای ساخت کاربر دیگر، تغییر رمز، نمایش یا غیرفعال‌کردن حساب‌ها:

```bash
.venv/bin/python scripts/manage_users.py add operator
.venv/bin/python scripts/manage_users.py add admin
.venv/bin/python scripts/manage_users.py list
.venv/bin/python scripts/manage_users.py disable operator
.venv/bin/python scripts/manage_users.py enable operator
```

اجرای مجدد دستور `add` برای یک نام کاربری موجود، رمز آن حساب را تغییر می‌دهد.

## ۶. تست ارتباط‌ها در حالت امن

```bash
cd /data/projects/rahband/backend
.venv/bin/python scripts/check_environment.py
```

در این مرحله `RAHBAND_SHADOW_MODE=true` است؛ بنابراین فرمان واقعی به راهبند فرستاده نمی‌شود.

## ۷. نصب و build فرانت

```bash
cd /data/projects/rahband/frontend
npm ci
npm run build
```

## ۸. نصب سرویس‌ها

```bash
cd /data/projects/rahband
sudo cp deploy/anpr.service /etc/systemd/system/anpr.service
sudo cp deploy/rahband-web.service /etc/systemd/system/rahband-web.service
sudo systemctl daemon-reload
sudo systemctl enable anpr.service
sudo systemctl enable rahband-web.service
sudo systemctl restart anpr.service
sudo systemctl restart rahband-web.service
```

بررسی وضعیت:

```bash
sudo systemctl status anpr.service --no-pager
sudo systemctl status rahband-web.service --no-pager
sudo journalctl -u anpr.service -f
```

برای خروج از نمایش زنده لاگ، `Ctrl+C` بزنید.

## ۹. نصب Nginx

```bash
cd /data/projects/rahband
sudo cp deploy/nginx-rahband.conf /etc/nginx/sites-available/rahband
sudo ln -sfn /etc/nginx/sites-available/rahband /etc/nginx/sites-enabled/rahband
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
```

اکنون برنامه باید از `http://172.18.1.35` باز شود.

## ۱۰. کنترل نهایی و فعال‌کردن راهبند

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/system/status
curl -I http://127.0.0.1:3000
curl -I http://172.18.1.35
```

بعد از اینکه تصویر هر سه دوربین، اتصال دیتابیس، ثبت تردد و دسترسی NRC تأیید شد:

```bash
cd /data/projects/rahband/backend
nano .env
```

مقدار زیر را تغییر دهید:

```dotenv
RAHBAND_SHADOW_MODE=false
```

سپس:

```bash
sudo systemctl restart anpr.service
sudo journalctl -u anpr.service -n 100 --no-pager
```

## فرمان‌های روزمره

```bash
sudo systemctl restart anpr.service
sudo systemctl restart rahband-web.service
sudo journalctl -u anpr.service -f
sudo journalctl -u rahband-web.service -f
sudo journalctl -u anpr.service --since today --no-pager
```

پس از هر تغییر بکند فقط `anpr.service` را restart کنید. پس از هر تغییر فرانت ابتدا `npm run build` و بعد `rahband-web.service` را restart کنید.
