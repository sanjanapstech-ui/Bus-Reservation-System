# Quick Setup Guide for Render.com (Flask + MySQL)

Render runs your Flask app as a Web Service. For MySQL, you typically use an **external/managed MySQL provider** (Railway, Aiven, DigitalOcean, AWS RDS, etc.), then connect to it from Render using environment variables.

## 1) Add environment variables in Render
Render Dashboard -> your Web Service -> **Environment**

### Required
- `SECRET_KEY` = a long random string (Flask sessions)

### Database (pick one option)
**Option A (recommended):**
- `MYSQL_URL` = `mysql://user:password@hostname:3306/database`

**Option B:**
- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DB`
- `MYSQL_PORT`

### Optional (recommended)
- `DIAGNOSTICS_TOKEN` = any random string  
  Protects `/db-config` and `/test-db` in production.

Click **Save Changes** and Render redeploys automatically.

## 2) Verify (optional)
If you set `DIAGNOSTICS_TOKEN`, open:
- `https://YOUR-APP.onrender.com/db-config?token=YOUR_DIAGNOSTICS_TOKEN`
- `https://YOUR-APP.onrender.com/test-db?token=YOUR_DIAGNOSTICS_TOKEN`

## Troubleshooting
- Still trying `localhost`: env vars not set (or not saved) in Render.
- Connection refused/timeout: wrong host/port or your DB provider blocks external connections.
- Auth failed: wrong username/password or wrong database name.
