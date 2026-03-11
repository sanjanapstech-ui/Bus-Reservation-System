# Database Configuration for Deployment

## Problem
If you see: `"Can't connect to MySQL server on 'localhost' (Connection refused)"`, your app is trying to connect to a database on `localhost`.

On Render/Vercel, `localhost` means **inside the deployed container/function**, not your laptop. So your app needs a database server that is reachable from the internet (or from the same private network).

## Important: MySQL vs MySQL Workbench
- **MySQL Workbench is only a GUI client.** It does not run the database.
- What must be running is the **MySQL Server** (`mysqld`) that your deployed app can reach.
- For deployment, use a **cloud/managed MySQL database** (Railway, Aiven, DigitalOcean, AWS RDS, etc.) that stays online 24/7.

## Solution: set environment variables
You must set `SECRET_KEY` and your database connection values in your hosting dashboard.

### Option A (recommended): `MYSQL_URL`
Set a single connection URL:
```
SECRET_KEY=your-random-secret-key
MYSQL_URL=mysql://user:password@hostname:3306/database
```

### Option B: separate variables
```
SECRET_KEY=your-random-secret-key
MYSQL_HOST=hostname
MYSQL_USER=user
MYSQL_PASSWORD=password
MYSQL_DB=database
MYSQL_PORT=3306
```

Alternative names are also supported by the app:
```
DB_HOST=hostname
DB_USER=user
DB_PASSWORD=password
DB_NAME=database
DB_PORT=3306
```

Optional (recommended for production):
```
DIAGNOSTICS_TOKEN=any-random-string
```
This protects `/db-config` and `/test-db` in production (otherwise they return 404).

## Render
1. Render Dashboard -> your Web Service
2. Go to **Environment**
3. Add the variables above (Option A or Option B)
4. Click **Save Changes** (Render redeploys automatically)

## Vercel
1. Vercel Project -> **Settings** -> **Environment Variables**
2. Add the variables above for Production/Preview/Development
3. Redeploy

## Local development
1. Copy `.env.example` to `.env`
2. Fill in your local MySQL values
3. Run:
   - `python app.py`
