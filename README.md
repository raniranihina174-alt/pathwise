# Pathwise

Pathwise is a Flask and SQLite starter for sharing student and graduate roadmaps, internships, and online courses.

## Run locally

1. Use Python 3.10 or newer.
2. Install Flask:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Set a persistent secret key in PowerShell, then start the app:

   ```powershell
   $env:SECRET_KEY = "replace-this-with-a-long-random-secret"
   python app.py
   ```

4. Open <http://127.0.0.1:5000>.

The SQLite database is created as `pathwise.db` next to `app.py`. Set `PATHWISE_DATABASE` to use a different database path. `FLASK_DEBUG=1` enables Flask debug mode for local development only.

## Admin dashboard

The admin account is configured separately from public student/company registration. Generate a password hash in PowerShell (enter the admin password when prompted):

```powershell
$env:ADMIN_EMAIL = "admin@example.com"
$env:ADMIN_PASSWORD_HASH = python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))"
python app.py
```

Open <http://127.0.0.1:5000/admin/login> and sign in with that email and password. Keep these environment variables private and use a strong password. The admin dashboard supports create/edit/delete for opportunities, editing account details, account activation controls, and listing visibility controls. Account and listing deletes permanently remove the associated account data or subscriptions, so those actions require confirmation.

## Included

- Student/graduate and company/educator registration, login, and account deactivation.
- Company dashboards to publish and edit internships or courses with detailed, line-by-line steps and official source links.
- Student subscriptions, in-app notifications when a followed listing changes, and a dashboard to manage followed paths.
- A protected admin dashboard to review platform activity, activate/deactivate accounts, and hide or restore public listings.
- Admin CRUD tools for opportunity listings and editable account profiles.
- Search and category/status filters for student and company dashboards, plus filtered CSV exports for administrators.
- Responsive mobile navigation, accessible keyboard interactions, and mobile-friendly dashboard layouts.
- Public opportunity browsing and searching.
- Public Home, About Us, and Services pages with shared navigation.
- Searchable career roadmaps with guided steps, external learning resources, internship links, and Google/YouTube search buttons; administrators can create, edit, publish, and remove roadmaps.
- Starter IT career paths include web, frontend, backend, full-stack, software, mobile, Python, Java, data, AI, cybersecurity, cloud, DevOps, networking, QA, databases, game development, IT support, systems administration, and IT business analysis.

Google and YouTube buttons open searches for the selected career; they do not import search results into Pathwise and do not require API keys. Roadmap starter content is added once to existing databases without replacing administrator edits. External organizations can be linked through the official source URL on each listing. This starter does not automatically import or monitor third-party websites, send email/SMS/push notifications, or verify company identities. In-app notifications are created when a publisher edits a listing.

For production, configure a stable secret key, run behind a production WSGI server with HTTPS, and add operational controls such as email delivery, backups, account recovery, and organization verification.
