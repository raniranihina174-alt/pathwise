import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode, urlparse

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("PATHWISE_DATABASE", BASE_DIR / "pathwise.db"))
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def connect_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('student', 'company')),
                organization TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('internship', 'course')),
                description TEXT NOT NULL,
                steps TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER NOT NULL REFERENCES users(id),
                resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, resource_id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS roadmaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                field TEXT NOT NULL,
                summary TEXT NOT NULL,
                description TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'Beginner',
                duration TEXT NOT NULL DEFAULT '',
                skills TEXT NOT NULL DEFAULT '',
                steps TEXT NOT NULL DEFAULT '',
                visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS roadmap_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roadmap_id INTEGER NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('learning', 'internship')),
                title TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                order_index INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS roadmap_seed_versions (
                version INTEGER PRIMARY KEY
            );
            """
        )
        resource_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(resources)")
        }
        if "visible" not in resource_columns:
            db.execute(
                "ALTER TABLE resources ADD COLUMN visible INTEGER NOT NULL DEFAULT 1"
            )
        seed_version = db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM roadmap_seed_versions"
        ).fetchone()[0]
        if seed_version < 1:
            seed_roadmaps(db)
            db.execute("INSERT INTO roadmap_seed_versions (version) VALUES (1)")


STARTER_ROADMAPS = [
    {
        "slug": "web-developer",
        "title": "Web Developer",
        "field": "Technology",
        "summary": "Go from web basics to building, shipping, and applying for your first developer role.",
        "description": "A practical beginner-friendly path through HTML, CSS, JavaScript, Git, responsive design, and portfolio projects. Learn by building real projects, then use the internship search links to find entry-level opportunities.",
        "level": "Beginner",
        "duration": "4–8 months",
        "skills": "HTML, CSS, JavaScript, Git, Responsive design, React",
        "steps": [
            "Learn how the web works: browsers, websites, and basic developer tools.",
            "Build page structure with semantic HTML and accessible forms.",
            "Style responsive layouts with CSS, Flexbox, and Grid.",
            "Learn JavaScript fundamentals and make interactive projects.",
            "Use Git and GitHub; publish a portfolio with three finished projects.",
            "Learn a UI library such as React and practice consuming an API.",
            "Prepare a focused CV, practice interviews, and apply to internships.",
        ],
        "learning": [
            ("MDN Web Development", "MDN", "Free guides for HTML, CSS, JavaScript, and web standards.", "https://developer.mozilla.org/en-US/docs/Learn_web_development"),
            ("Responsive Web Design", "freeCodeCamp", "Practice HTML and CSS through hands-on lessons and projects.", "https://www.freecodecamp.org/learn/2022/responsive-web-design/"),
            ("Frontend Developer Roadmap", "roadmap.sh", "A visual checklist of frontend technologies and concepts.", "https://roadmap.sh/frontend"),
        ],
        "internships": [
            ("Frontend internships", "LinkedIn Jobs", "Search current frontend developer internships; review each employer's official requirements.", "https://www.linkedin.com/jobs/search/?keywords=frontend%20developer%20intern"),
            ("Web developer internships", "Indeed", "Browse web development internship listings and application details.", "https://www.indeed.com/jobs?q=web+developer+intern"),
        ],
    },
    {
        "slug": "data-analyst",
        "title": "Data Analyst",
        "field": "Data",
        "summary": "Learn spreadsheets, SQL, analytics, and visualisation, then build a portfolio for entry-level roles.",
        "description": "A guided path for learning to turn raw information into useful insights. Practice with spreadsheets, SQL, a programming language, and a dashboard tool. Build small case studies that explain your findings clearly.",
        "level": "Beginner",
        "duration": "3–6 months",
        "skills": "Excel, SQL, Python, Statistics, Data visualisation, Communication",
        "steps": [
            "Learn spreadsheet formulas, sorting, filtering, and pivot tables.",
            "Study basic descriptive statistics and how to ask measurable questions.",
            "Learn SQL: SELECT, filtering, joins, grouping, and subqueries.",
            "Use Python and pandas to clean and explore a small dataset.",
            "Create clear charts and a dashboard; explain the decisions they support.",
            "Publish two case studies with your question, method, results, and limitations.",
            "Practice explaining your work and apply for analyst internships.",
        ],
        "learning": [
            ("SQLBolt", "SQLBolt", "Interactive lessons to practice SQL queries in your browser.", "https://sqlbolt.com/"),
            ("Python tutorials", "Kaggle Learn", "Short practical courses for Python, pandas, and data visualisation.", "https://www.kaggle.com/learn"),
            ("Data analysis learning path", "Microsoft Learn", "Explore guided learning for data analytics and Power BI.", "https://learn.microsoft.com/en-us/training/powerplatform/power-bi/"),
        ],
        "internships": [
            ("Data analyst internships", "LinkedIn Jobs", "Search current data analyst internships and check each employer's requirements.", "https://www.linkedin.com/jobs/search/?keywords=data%20analyst%20intern"),
            ("Data analytics internships", "Indeed", "Browse data analytics internship roles and application details.", "https://www.indeed.com/jobs?q=data+analyst+intern"),
        ],
    },
    {
        "slug": "ui-ux-designer",
        "title": "UI/UX Designer",
        "field": "Design",
        "summary": "Practice user research, wireframes, prototypes, and case studies to prepare for design internships.",
        "description": "Learn how to understand user needs and turn them into accessible, clear interfaces. Follow the design process from research through wireframes and prototypes, then document your decisions in a small portfolio.",
        "level": "Beginner",
        "duration": "3–6 months",
        "skills": "User research, Wireframing, Figma, Prototyping, Accessibility, Design systems",
        "steps": [
            "Learn the difference between user experience, interaction, and interface design.",
            "Practice observing users, writing interview questions, and summarising needs.",
            "Sketch user flows and low-fidelity wireframes for a simple problem.",
            "Learn Figma basics and create an interactive prototype.",
            "Check accessibility, responsive layouts, and common usability issues.",
            "Write two concise case studies that explain your process and trade-offs.",
            "Share your portfolio, prepare to discuss your work, and apply for internships.",
        ],
        "learning": [
            ("Figma Learn", "Figma", "Official lessons and resources for interface design and prototyping.", "https://www.figma.com/resource-library/"),
            ("Laws of UX", "Laws of UX", "A practical introduction to common user experience principles.", "https://lawsofux.com/"),
            ("Accessibility fundamentals", "W3C WAI", "Official guidance for making web content accessible.", "https://www.w3.org/WAI/fundamentals/"),
        ],
        "internships": [
            ("UX design internships", "LinkedIn Jobs", "Search current UX and product design internships.", "https://www.linkedin.com/jobs/search/?keywords=ux%20design%20intern"),
            ("UI/UX internships", "Indeed", "Browse UI/UX design internships and employer application details.", "https://www.indeed.com/jobs?q=UX+design+intern"),
        ],
    },
]


IT_SPECIALTY_ROADMAPS = [
    {
        "slug": "frontend-developer",
        "title": "Frontend Developer",
        "field": "Software Development",
        "duration": "4–8 months",
        "skills": "HTML, CSS, JavaScript, Accessibility, React, Git",
        "summary": "Build accessible, responsive websites and interactive user interfaces.",
        "steps": [
            "Learn semantic HTML, browser tools, and how pages are structured.",
            "Practice CSS layouts, responsive design, Flexbox, and Grid.",
            "Learn JavaScript fundamentals, DOM events, and asynchronous requests.",
            "Build accessible pages that work with a keyboard and different screen sizes.",
            "Learn a framework such as React and connect a project to a public API.",
            "Publish three polished projects and document your decisions.",
            "Prepare your portfolio and apply for junior frontend roles or internships.",
        ],
        "learning": ("Frontend learning path", "MDN", "Learn core HTML, CSS, JavaScript, and web development skills.", "https://developer.mozilla.org/en-US/docs/Learn_web_development"),
    },
    {
        "slug": "backend-developer",
        "title": "Backend Developer",
        "field": "Software Development",
        "duration": "5–9 months",
        "skills": "Python, APIs, SQL, Databases, Authentication, Testing",
        "summary": "Learn to build secure APIs, work with databases, and power web applications.",
        "steps": [
            "Learn one backend language and practice functions, modules, and error handling.",
            "Understand HTTP, JSON, requests, responses, and REST API design.",
            "Use SQL and a relational database to model and query application data.",
            "Build authentication and authorization with secure password handling.",
            "Add input validation, automated tests, and clear error responses.",
            "Deploy an API and database, then document the endpoints and setup.",
            "Practice code reviews and apply for backend internships or junior roles.",
        ],
        "learning": ("Backend Developer Roadmap", "roadmap.sh", "Review the main backend concepts and technologies in a guided order.", "https://roadmap.sh/backend"),
    },
    {
        "slug": "full-stack-developer",
        "title": "Full-Stack Developer",
        "field": "Software Development",
        "duration": "6–12 months",
        "skills": "HTML, CSS, JavaScript, React, APIs, SQL, Git",
        "summary": "Create complete web applications from the user interface to the database.",
        "steps": [
            "Learn HTML, CSS, JavaScript, and Git before choosing a framework.",
            "Build responsive interfaces and practice component-based UI development.",
            "Create a backend API and understand HTTP, JSON, and common status codes.",
            "Connect the application to a SQL database and model its data.",
            "Add authentication, form validation, and automated tests.",
            "Deploy a complete application and write a clear project README.",
            "Show two end-to-end projects in a portfolio and apply for junior roles.",
        ],
        "learning": ("Full Stack Developer Roadmap", "roadmap.sh", "Follow a practical overview of frontend, backend, and deployment topics.", "https://roadmap.sh/full-stack"),
    },
    {
        "slug": "software-engineer",
        "title": "Software Engineer",
        "field": "Software Development",
        "duration": "6–12 months",
        "skills": "Programming, Data structures, Algorithms, Git, Testing, System design",
        "summary": "Build strong programming foundations and learn to design reliable software.",
        "steps": [
            "Choose a programming language and learn its core syntax and standard library.",
            "Practice problem solving with arrays, maps, queues, trees, and algorithms.",
            "Use Git branches, code reviews, debugging tools, and automated tests.",
            "Learn clean code, modular design, and common software architecture patterns.",
            "Build an application that stores data and handles realistic failure cases.",
            "Learn basic deployment, monitoring, and how to explain engineering trade-offs.",
            "Prepare project walkthroughs and practice entry-level technical interviews.",
        ],
        "learning": ("Software Engineer Roadmap", "roadmap.sh", "Explore foundational software engineering skills and development practices.", "https://roadmap.sh/software-engineer"),
    },
    {
        "slug": "mobile-app-developer",
        "title": "Mobile App Developer",
        "field": "Software Development",
        "duration": "5–10 months",
        "skills": "Kotlin, Swift, Flutter, UI design, APIs, Mobile testing",
        "summary": "Design, build, and test mobile apps for Android or iOS devices.",
        "steps": [
            "Learn programming fundamentals and choose Android, iOS, or cross-platform development.",
            "Study mobile layouts, navigation, accessibility, and platform conventions.",
            "Build small apps that store data locally and handle different screen sizes.",
            "Connect an app to an API and handle loading, errors, and offline states.",
            "Practice automated tests, permissions, privacy, and secure data storage.",
            "Publish a demo build and document the app with screenshots and setup steps.",
            "Prepare a mobile portfolio and apply for junior mobile developer roles.",
        ],
        "learning": ("Android learning paths", "Android Developers", "Use official Android guides and beginner learning paths.", "https://developer.android.com/courses"),
    },
    {
        "slug": "python-developer",
        "title": "Python Developer",
        "field": "Software Development",
        "duration": "4–8 months",
        "skills": "Python, Git, APIs, SQL, Testing, Packaging",
        "summary": "Use Python to build maintainable applications, scripts, and web services.",
        "steps": [
            "Learn Python syntax, collections, functions, modules, and exceptions.",
            "Practice reading files, using packages, and writing reusable code.",
            "Learn Git and create automated tests for your programs.",
            "Use SQL and connect a Python application to a database.",
            "Build a small API or command-line tool with clear input validation.",
            "Package and deploy a project; document how another developer can run it.",
            "Publish several projects and apply for Python developer internships or roles.",
        ],
        "learning": ("Python Tutorial", "Python.org", "Work through the official Python tutorial and language basics.", "https://docs.python.org/3/tutorial/"),
    },
    {
        "slug": "java-developer",
        "title": "Java Developer",
        "field": "Software Development",
        "duration": "5–9 months",
        "skills": "Java, OOP, Collections, SQL, Spring, Testing",
        "summary": "Learn Java programming and build tested applications and backend services.",
        "steps": [
            "Learn Java syntax, object-oriented programming, and the standard library.",
            "Practice collections, exceptions, generics, and basic data structures.",
            "Use Git, a build tool, and unit tests in small projects.",
            "Learn SQL and build an application that persists data.",
            "Explore a Java web framework and create a small REST API.",
            "Deploy your service and document its design and endpoints.",
            "Prepare project examples and apply for junior Java roles.",
        ],
        "learning": ("Learn Java", "dev.java", "Use the official Java learning site for language fundamentals and practice.", "https://dev.java/learn/"),
    },
    {
        "slug": "data-scientist",
        "title": "Data Scientist",
        "field": "Data & Artificial Intelligence",
        "duration": "6–12 months",
        "skills": "Python, Statistics, SQL, Data cleaning, Machine learning, Communication",
        "summary": "Turn data into evidence-based insights and explain findings clearly.",
        "steps": [
            "Learn Python, notebooks, SQL, and how to inspect unfamiliar datasets.",
            "Study probability, descriptive statistics, and experimental reasoning.",
            "Clean data, handle missing values, and document assumptions.",
            "Explore data visually and communicate patterns without overstating results.",
            "Train and evaluate baseline machine-learning models on suitable datasets.",
            "Publish two reproducible case studies that explain limitations and conclusions.",
            "Practice presenting findings and apply for data science internships or roles.",
        ],
        "learning": ("Data science courses", "Kaggle Learn", "Practice Python, pandas, visualisation, and introductory machine learning.", "https://www.kaggle.com/learn"),
    },
    {
        "slug": "machine-learning-engineer",
        "title": "Machine Learning Engineer",
        "field": "Data & Artificial Intelligence",
        "duration": "8–14 months",
        "skills": "Python, Statistics, Machine learning, APIs, Testing, Model deployment",
        "summary": "Train, evaluate, and deploy machine-learning models as reliable software.",
        "steps": [
            "Build Python programming, SQL, and data-wrangling foundations.",
            "Study statistics, validation, and how to prevent data leakage.",
            "Train baseline models and compare them using appropriate evaluation metrics.",
            "Learn a machine-learning framework and track experiments reproducibly.",
            "Wrap a model in a tested API and manage model inputs safely.",
            "Deploy a demo and monitor model quality, latency, and limitations.",
            "Present an end-to-end project and apply for entry-level ML roles.",
        ],
        "learning": ("Machine Learning Crash Course", "Google", "Study practical machine-learning concepts and guided exercises.", "https://developers.google.com/machine-learning/crash-course"),
    },
    {
        "slug": "ai-engineer",
        "title": "AI Engineer",
        "field": "Data & Artificial Intelligence",
        "duration": "6–12 months",
        "skills": "Python, APIs, Machine learning, Data handling, Evaluation, Responsible AI",
        "summary": "Build useful AI-powered applications and evaluate their quality and limits.",
        "steps": [
            "Learn Python, HTTP APIs, JSON, and basic data-handling practices.",
            "Understand machine-learning concepts, model evaluation, and common limitations.",
            "Build a small application that uses a documented AI service or open model.",
            "Design clear prompts and test outputs against realistic examples.",
            "Add privacy safeguards, input validation, and human review for important decisions.",
            "Measure quality, cost, and latency; document failure cases and limitations.",
            "Publish a responsible AI project and apply for junior AI application roles.",
        ],
        "learning": ("AI learning hub", "Microsoft Learn", "Explore guided modules for AI concepts and responsible AI practices.", "https://learn.microsoft.com/en-us/training/ai/"),
    },
    {
        "slug": "data-engineer",
        "title": "Data Engineer",
        "field": "Data & Artificial Intelligence",
        "duration": "6–12 months",
        "skills": "SQL, Python, Data modelling, ETL, Cloud, Data quality",
        "summary": "Build dependable data pipelines that prepare information for analysis.",
        "steps": [
            "Learn SQL, Python, Git, and how relational data is stored.",
            "Practice data modelling, schema design, and query performance basics.",
            "Build a pipeline that collects, validates, and transforms public sample data.",
            "Learn batch processing, scheduling, logging, and safe retry behaviour.",
            "Explore a cloud data platform and access controls using sample projects.",
            "Test data quality and document pipeline assumptions and failure handling.",
            "Publish a reproducible pipeline project and apply for junior data engineering roles.",
        ],
        "learning": ("Data Engineer Roadmap", "roadmap.sh", "Review common data engineering tools and concepts in a learning sequence.", "https://roadmap.sh/data-engineer"),
    },
    {
        "slug": "cybersecurity-analyst",
        "title": "Cybersecurity Analyst",
        "field": "Cybersecurity",
        "duration": "6–12 months",
        "skills": "Networking, Linux, Security fundamentals, Log analysis, Incident response",
        "summary": "Learn defensive security, investigate alerts, and help protect systems and data.",
        "steps": [
            "Learn networking, operating-system basics, and how common web services work.",
            "Study security principles, access control, patching, and safe configuration.",
            "Practice Linux and Windows log review in legal training labs or sample datasets.",
            "Learn to triage alerts, record evidence, and follow an incident-response process.",
            "Study vulnerability management and responsible disclosure fundamentals.",
            "Build a defensive lab portfolio with clear notes and no testing of real systems.",
            "Prepare for entry-level security certifications and apply for SOC internships.",
        ],
        "learning": ("SOC Level 1 learning path", "TryHackMe", "Practice defensive security concepts in guided, authorized training labs.", "https://tryhackme.com/path/outline/soclevel1"),
    },
    {
        "slug": "cloud-engineer",
        "title": "Cloud Engineer",
        "field": "Cloud & Infrastructure",
        "duration": "6–12 months",
        "skills": "Linux, Networking, Cloud services, IAM, Scripting, Monitoring",
        "summary": "Learn to deploy, secure, and monitor applications on cloud platforms.",
        "steps": [
            "Learn Linux, networking, DNS, HTTP, and command-line fundamentals.",
            "Choose one cloud provider and study its core compute, storage, and network services.",
            "Practice identity and access management with least-privilege permissions.",
            "Deploy a small application and configure basic monitoring and alerts.",
            "Learn infrastructure-as-code fundamentals using a safe practice account.",
            "Document costs, security choices, backups, and recovery steps.",
            "Build a cloud project portfolio and apply for junior cloud roles.",
        ],
        "learning": ("AWS Cloud Practitioner training", "AWS", "Start with foundational cloud concepts and official learning resources.", "https://aws.amazon.com/training/learn-about/cloud-practitioner/"),
    },
    {
        "slug": "devops-engineer",
        "title": "DevOps Engineer",
        "field": "Cloud & Infrastructure",
        "duration": "6–12 months",
        "skills": "Linux, Git, CI/CD, Containers, Cloud, Monitoring",
        "summary": "Automate software delivery and improve the reliability of deployed services.",
        "steps": [
            "Learn Linux, networking, shell scripting, and version control with Git.",
            "Understand how applications are built, tested, and released.",
            "Create a CI pipeline that runs tests and checks on a sample project.",
            "Learn container fundamentals and run a service locally.",
            "Deploy a small application and configure logs, monitoring, and alerts.",
            "Practice secrets management, backups, and safe rollback procedures.",
            "Document your delivery pipeline and apply for junior platform or DevOps roles.",
        ],
        "learning": ("DevOps Engineer Roadmap", "roadmap.sh", "Explore automation, deployment, infrastructure, and reliability topics.", "https://roadmap.sh/devops"),
    },
    {
        "slug": "network-engineer",
        "title": "Network Engineer",
        "field": "Cloud & Infrastructure",
        "duration": "5–10 months",
        "skills": "TCP/IP, Routing, Switching, DNS, Firewalls, Troubleshooting",
        "summary": "Understand computer networks and practice diagnosing connectivity problems.",
        "steps": [
            "Learn network layers, IPv4/IPv6 addressing, subnetting, and common protocols.",
            "Study switching, routing, DNS, DHCP, and wireless fundamentals.",
            "Build practice topologies in a simulator or authorized home lab.",
            "Learn firewall concepts, segmentation, and secure network configuration.",
            "Practice troubleshooting using a structured process and clear documentation.",
            "Prepare for an entry-level networking certification if useful in your region.",
            "Build a lab portfolio and apply for network support or junior engineer roles.",
        ],
        "learning": ("Networking courses", "Cisco Skills for All", "Start with guided, beginner-friendly networking and cybersecurity lessons.", "https://skillsforall.com/"),
    },
    {
        "slug": "qa-engineer",
        "title": "QA Engineer",
        "field": "Software Quality",
        "duration": "4–8 months",
        "skills": "Test design, Bug reports, Web testing, APIs, Automation, SQL",
        "summary": "Help teams release dependable software through thoughtful testing and automation.",
        "steps": [
            "Learn software development lifecycles and the purpose of different test levels.",
            "Practice writing clear test cases and reproducible bug reports.",
            "Test web forms, responsive behaviour, accessibility, and error states.",
            "Learn API testing and basic SQL for checking application data.",
            "Automate a small set of browser tests using a common testing framework.",
            "Run tests in a CI workflow and explain what should remain manual.",
            "Publish sample test plans and apply for QA internships or junior roles.",
        ],
        "learning": ("Software testing guide", "BrowserStack", "Explore testing fundamentals, test types, and quality practices.", "https://www.browserstack.com/guide/software-testing"),
    },
    {
        "slug": "database-administrator",
        "title": "Database Administrator",
        "field": "Data & Databases",
        "duration": "5–10 months",
        "skills": "SQL, PostgreSQL, Backups, Indexes, Access control, Monitoring",
        "summary": "Learn to maintain databases, protect data, and support reliable applications.",
        "steps": [
            "Learn relational database concepts, SQL, tables, and constraints.",
            "Install a local database and practice users, roles, and permissions.",
            "Study indexes, query plans, and basic performance troubleshooting.",
            "Practice backups and restoring a database in a local environment.",
            "Learn transactions, monitoring, upgrades, and safe change procedures.",
            "Document a small database deployment and its recovery plan.",
            "Prepare for database support and junior administration roles.",
        ],
        "learning": ("PostgreSQL tutorial", "PostgreSQL", "Learn relational database concepts and SQL using the official tutorial.", "https://www.postgresql.org/docs/current/tutorial.html"),
    },
    {
        "slug": "game-developer",
        "title": "Game Developer",
        "field": "Game Development",
        "duration": "6–12 months",
        "skills": "C# or C++, Game engine, Physics, Git, Debugging, Level design",
        "summary": "Learn programming and game-engine basics by building small playable games.",
        "steps": [
            "Choose a game engine and learn its editor, scenes, and scripting language.",
            "Practice programming fundamentals through small gameplay mechanics.",
            "Build a simple playable prototype with input, movement, and feedback.",
            "Learn game objects, collisions, UI, audio, and basic performance checks.",
            "Use Git and collaborate safely on a small project.",
            "Publish a polished demo and explain your role and design decisions.",
            "Prepare a portfolio and apply for game development internships or junior roles.",
        ],
        "learning": ("Unity Learn", "Unity", "Follow beginner tutorials and guided projects in the Unity game engine.", "https://learn.unity.com/"),
    },
    {
        "slug": "it-support-specialist",
        "title": "IT Support Specialist",
        "field": "IT Operations",
        "duration": "3–6 months",
        "skills": "Operating systems, Networking, Troubleshooting, Security, Communication",
        "summary": "Build the technical and communication skills needed to support computer users.",
        "steps": [
            "Learn computer hardware, operating systems, accounts, and common peripherals.",
            "Study networking basics, Wi-Fi, DNS, and connectivity troubleshooting.",
            "Practice a clear support workflow: ask, reproduce, diagnose, resolve, document.",
            "Learn account security, permissions, updates, backups, and safe handling of data.",
            "Practice help-desk ticket writing and explaining fixes in plain language.",
            "Build a home lab and document a few sample troubleshooting cases.",
            "Prepare for entry-level IT certifications and apply for support roles.",
        ],
        "learning": ("Google IT Support Certificate", "Google", "Review foundational IT support topics and troubleshooting skills.", "https://www.coursera.org/professional-certificates/google-it-support"),
    },
    {
        "slug": "systems-administrator",
        "title": "Systems Administrator",
        "field": "IT Operations",
        "duration": "5–10 months",
        "skills": "Linux, Windows, Networking, Scripting, Backups, Monitoring",
        "summary": "Learn to administer operating systems and keep services available and secure.",
        "steps": [
            "Learn Linux and Windows administration basics in a local practice environment.",
            "Study users, groups, permissions, storage, services, and system logs.",
            "Practice networking, DNS, and remote administration fundamentals.",
            "Automate repetitive tasks with a beginner-friendly scripting language.",
            "Configure backups, updates, monitoring, and documented recovery procedures.",
            "Build a small authorized lab and write clear operational runbooks.",
            "Apply for junior systems administration or IT operations roles.",
        ],
        "learning": ("Ubuntu Server tutorials", "Ubuntu", "Use official tutorials to practice Linux server administration.", "https://ubuntu.com/server/docs"),
    },
    {
        "slug": "it-business-analyst",
        "title": "IT Business Analyst",
        "field": "IT & Product",
        "duration": "4–8 months",
        "skills": "Requirements, Process mapping, SQL basics, Communication, User stories, Testing",
        "summary": "Connect user needs and business goals to clear software requirements.",
        "steps": [
            "Learn how software teams plan, build, test, and release products.",
            "Practice stakeholder interviews and writing measurable requirements.",
            "Map a simple process and identify user needs and constraints.",
            "Write user stories, acceptance criteria, and clear workflow diagrams.",
            "Learn basic SQL and help validate that a feature meets its requirements.",
            "Create a sample requirements pack and explain trade-offs clearly.",
            "Build a portfolio case study and apply for junior analyst roles.",
        ],
        "learning": ("Business analyst roadmap", "roadmap.sh", "Review common business analysis skills and responsibilities.", "https://roadmap.sh/business-analyst"),
    },
]


def all_seed_roadmaps():
    roadmaps = list(STARTER_ROADMAPS)
    for specialty in IT_SPECIALTY_ROADMAPS:
        query = urlencode({"keywords": f"{specialty['title']} intern"})
        internship_query = urlencode({"q": f"{specialty['title']} intern"})
        roadmaps.append(
            {
                **specialty,
                "learning": [specialty["learning"]],
                "description": (
                    f"A practical beginner-friendly roadmap for becoming a {specialty['title']}. "
                    "Follow the steps, practice with projects, and check each external provider "
                    "for current course and job details."
                ),
                "level": "Beginner",
                "internships": [
                    (
                        f"{specialty['title']} internships",
                        "LinkedIn Jobs",
                        "Search current openings and confirm eligibility and application details with each employer.",
                        "https://www.linkedin.com/jobs/search/?" + query,
                    ),
                    (
                        f"{specialty['title']} internships",
                        "Indeed",
                        "Browse current listings and verify requirements and deadlines with each employer.",
                        "https://www.indeed.com/jobs?" + internship_query,
                    ),
                ],
            }
        )
    return roadmaps


def seed_roadmaps(db):
    for roadmap in all_seed_roadmaps():
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO roadmaps
                (slug, title, field, summary, description, level, duration, skills, steps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                roadmap["slug"], roadmap["title"], roadmap["field"],
                roadmap["summary"], roadmap["description"], roadmap["level"],
                roadmap["duration"], roadmap["skills"], "\n".join(roadmap["steps"]),
            ),
        )
        if cursor.rowcount == 0:
            continue
        roadmap_id = cursor.lastrowid
        for kind in ("learning", "internships"):
            for index, (title, provider, description, url) in enumerate(roadmap[kind]):
                db.execute(
                    """
                    INSERT INTO roadmap_links
                        (roadmap_id, kind, title, provider, description, url, order_index)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        roadmap_id,
                        "internship" if kind == "internships" else "learning",
                        title, provider, description, url, index,
                    ),
                )


initialize_db()


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def prepare_request():
    g.user = None
    if session.get("user_id"):
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ? AND active = 1",
            (session["user_id"],),
        ).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            abort(400, description="Your form session expired. Please reload and try again.")


@app.context_processor
def template_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    unread_count = 0
    if g.user and g.user["role"] == "student":
        unread_count = get_db().execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
            (g.user["id"],),
        ).fetchone()[0]
    return {
        "current_user": g.user,
        "current_admin": session.get("admin_authenticated") is True,
        "admin_email": session.get("admin_email", ""),
        "csrf_token": session["csrf_token"],
        "unread_count": unread_count,
    }


def admin_credentials_configured():
    return bool(os.environ.get("ADMIN_EMAIL") and os.environ.get("ADMIN_PASSWORD_HASH"))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(role):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not admin_credentials_configured():
            session.clear()
            flash("Admin access is not configured. Set ADMIN_EMAIL and ADMIN_PASSWORD_HASH.", "error")
            return redirect(url_for("admin_login"))
        if session.get("admin_authenticated") is not True:
            flash("Log in with your admin account to continue.", "info")
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped


def valid_http_url(value):
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_resource(resource_id, include_hidden=False):
    visibility_filter = "" if include_hidden else "AND resources.visible = 1"
    resource = get_db().execute(
        f"""
        SELECT resources.*, users.name AS publisher, users.organization
        FROM resources JOIN users ON users.id = resources.owner_id
        WHERE resources.id = ? AND users.active = 1 {visibility_filter}
        """,
        (resource_id,),
    ).fetchone()
    if resource is None:
        abort(404)
    return resource


def roadmap_slug(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80] or secrets.token_hex(6)


def roadmap_links_from_form(field_name):
    links = []
    for line_number, raw_line in enumerate(request.form.get(field_name, "").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|", 3)]
        if len(parts) != 4 or not all(parts[:3]) or not valid_http_url(parts[2]):
            raise ValueError(
                f"Resource line {line_number} must be: Title | Provider | https://URL | Description"
            )
        links.append(
            {"title": parts[0], "provider": parts[1], "url": parts[2], "description": parts[3]}
        )
    return links


def roadmap_link_text(links):
    return "\n".join(
        f"{link['title']} | {link['provider']} | {link['url']} | {link['description']}"
        for link in links
    )


def get_roadmap_form_values():
    return {
        "title": request.form.get("title", "").strip(),
        "field": request.form.get("field", "").strip(),
        "summary": request.form.get("summary", "").strip(),
        "description": request.form.get("description", "").strip(),
        "level": request.form.get("level", "Beginner").strip(),
        "duration": request.form.get("duration", "").strip(),
        "skills": request.form.get("skills", "").strip(),
        "steps": request.form.get("steps", "").strip(),
        "visible": 1 if request.form.get("visible", "1") == "1" else 0,
    }


def get_roadmap_links_form_values():
    return {
        "learning": roadmap_links_from_form("learning_links"),
        "internship": roadmap_links_from_form("internship_links"),
    }


def validate_roadmap(values):
    for field in ("title", "field", "summary", "description", "steps"):
        if not values[field]:
            return f"Please fill in the roadmap {field}."
    if len(values["title"]) > 120 or len(values["summary"]) > 300:
        return "Title must be under 120 characters and summary under 300 characters."
    return None


@app.route("/")
def home():
    category = request.args.get("category", "")
    search = request.args.get("q", "").strip()
    query = """
        SELECT resources.*, users.name AS publisher, users.organization,
               (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id) AS subscriber_count
        FROM resources JOIN users ON users.id = resources.owner_id
        WHERE users.active = 1 AND resources.visible = 1
    """
    params = []
    if category in {"internship", "course"}:
        query += " AND resources.category = ?"
        params.append(category)
    if search:
        query += " AND (resources.title LIKE ? OR resources.description LIKE ? OR users.organization LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term])
    query += " ORDER BY resources.updated_at DESC, resources.id DESC"
    db = get_db()
    resources = db.execute(query, params).fetchall()
    roadmaps = db.execute(
        """
        SELECT slug, title, field, summary, level, duration, skills
        FROM roadmaps WHERE visible = 1 ORDER BY title LIMIT 6
        """
    ).fetchall()
    return render_template(
        "home.html", resources=resources, category=category, search=search, roadmaps=roadmaps
    )


@app.route("/roadmaps")
def roadmap_index():
    search = request.args.get("q", "").strip()
    query = """
        SELECT slug, title, field, summary, level, duration, skills
        FROM roadmaps WHERE visible = 1
    """
    params = []
    if search:
        query += " AND (title LIKE ? OR field LIKE ? OR summary LIKE ? OR skills LIKE ? OR description LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term, term])
    query += " ORDER BY title"
    roadmaps = get_db().execute(query, params).fetchall()
    return render_template("roadmaps.html", roadmaps=roadmaps, search=search)


@app.route("/roadmaps/<slug>")
def roadmap_detail(slug):
    roadmap = get_db().execute(
        "SELECT * FROM roadmaps WHERE slug = ? AND visible = 1", (slug,)
    ).fetchone()
    if roadmap is None:
        abort(404)
    links = get_db().execute(
        "SELECT * FROM roadmap_links WHERE roadmap_id = ? ORDER BY kind, order_index, id",
        (roadmap["id"],),
    ).fetchall()
    learning_links = [link for link in links if link["kind"] == "learning"]
    internship_links = [link for link in links if link["kind"] == "internship"]
    more_roadmaps = get_db().execute(
        """
        SELECT slug, title, field, summary FROM roadmaps
        WHERE visible = 1 AND id != ? ORDER BY title LIMIT 3
        """,
        (roadmap["id"],),
    ).fetchall()
    return render_template(
        "roadmap_detail.html",
        roadmap=roadmap,
        steps=[step.strip() for step in roadmap["steps"].splitlines() if step.strip()],
        skills=[skill.strip() for skill in roadmap["skills"].split(",") if skill.strip()],
        learning_links=learning_links,
        internship_links=internship_links,
        google_search_url="https://www.google.com/search?" + urlencode(
            {"q": f"{roadmap['title']} {roadmap['field']} career roadmap learning resources"}
        ),
        youtube_search_url="https://www.youtube.com/results?" + urlencode(
            {"search_query": f"{roadmap['title']} {roadmap['field']} roadmap tutorial"}
        ),
        more_roadmaps=more_roadmaps,
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/services")
def services():
    return render_template("services.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if admin_credentials_configured() and session.get("admin_authenticated") is True:
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not admin_email or not password_hash:
            flash("Admin access is not configured. Set ADMIN_EMAIL and ADMIN_PASSWORD_HASH.", "error")
        elif (
            hmac.compare_digest(email, admin_email)
            and check_password_hash(password_hash, password)
        ):
            session.clear()
            session["admin_authenticated"] = True
            session["admin_email"] = admin_email
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Admin email or password is incorrect.", "error")
    return render_template(
        "admin_login.html", admin_configured=admin_credentials_configured()
    )


@app.post("/admin/logout")
@admin_required
def admin_logout():
    session.clear()
    flash("You have been logged out of the admin dashboard.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    db = get_db()
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    category = request.args.get("category", "all")

    stats = {
        "students": db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'student' AND active = 1"
        ).fetchone()[0],
        "companies": db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'company' AND active = 1"
        ).fetchone()[0],
        "inactive_accounts": db.execute(
            "SELECT COUNT(*) FROM users WHERE active = 0"
        ).fetchone()[0],
        "roadmaps": db.execute(
            "SELECT COUNT(*) FROM roadmaps WHERE visible = 1"
        ).fetchone()[0],
        "internships": db.execute(
            """
            SELECT COUNT(*) FROM resources JOIN users ON users.id = resources.owner_id
            WHERE resources.category = 'internship' AND resources.visible = 1 AND users.active = 1
            """
        ).fetchone()[0],
        "courses": db.execute(
            """
            SELECT COUNT(*) FROM resources JOIN users ON users.id = resources.owner_id
            WHERE resources.category = 'course' AND resources.visible = 1 AND users.active = 1
            """
        ).fetchone()[0],
    }

    account_query = "SELECT * FROM users WHERE 1 = 1"
    account_params = []
    if status == "active":
        account_query += " AND active = 1"
    elif status == "inactive":
        account_query += " AND active = 0"
    if search:
        account_query += " AND (name LIKE ? OR email LIKE ? OR organization LIKE ?)"
        term = f"%{search}%"
        account_params.extend([term, term, term])
    account_query += " ORDER BY created_at DESC, id DESC LIMIT 100"
    accounts = db.execute(account_query, account_params).fetchall()

    resource_query = """
        SELECT resources.*, users.name AS publisher, users.organization, users.active AS publisher_active,
               (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id) AS subscriber_count
        FROM resources JOIN users ON users.id = resources.owner_id WHERE 1 = 1
    """
    resource_params = []
    if status == "active":
        resource_query += " AND resources.visible = 1"
    elif status == "inactive":
        resource_query += " AND resources.visible = 0"
    if category in {"internship", "course"}:
        resource_query += " AND resources.category = ?"
        resource_params.append(category)
    if search:
        resource_query += " AND (resources.title LIKE ? OR users.name LIKE ? OR users.organization LIKE ?)"
        term = f"%{search}%"
        resource_params.extend([term, term, term])
    resource_query += " ORDER BY resources.updated_at DESC, resources.id DESC LIMIT 100"
    resources = db.execute(resource_query, resource_params).fetchall()
    roadmap_query = """
        SELECT id, slug, title, field, summary, visible, updated_at
        FROM roadmaps WHERE 1 = 1
    """
    roadmap_params = []
    if status == "active":
        roadmap_query += " AND visible = 1"
    elif status == "inactive":
        roadmap_query += " AND visible = 0"
    if search:
        roadmap_query += " AND (title LIKE ? OR field LIKE ? OR summary LIKE ? OR skills LIKE ?)"
        term = f"%{search}%"
        roadmap_params.extend([term, term, term, term])
    roadmap_query += " ORDER BY updated_at DESC, title LIMIT 100"
    roadmaps = db.execute(roadmap_query, roadmap_params).fetchall()
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        accounts=accounts,
        resources=resources,
        roadmaps=roadmaps,
        search=search,
        status=status,
        category=category,
    )


@app.get("/admin/export/<dataset>")
@admin_required
def admin_export(dataset):
    if dataset not in {"accounts", "opportunities"}:
        abort(404)

    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    category = request.args.get("category", "all")
    db = get_db()

    if dataset == "accounts":
        query = """
            SELECT name, email, role, organization, active, created_at
            FROM users WHERE 1 = 1
        """
        params = []
        if status == "active":
            query += " AND active = 1"
        elif status == "inactive":
            query += " AND active = 0"
        if search:
            query += " AND (name LIKE ? OR email LIKE ? OR organization LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        query += " ORDER BY created_at DESC, id DESC"
        headers = ["Name", "Email", "Account type", "Organization", "Active", "Created"]
        rows = db.execute(query, params).fetchall()
        values = (
            (row["name"], row["email"], row["role"], row["organization"], row["active"], row["created_at"])
            for row in rows
        )
    else:
        query = """
            SELECT resources.title, resources.category, resources.description,
                   resources.location, resources.deadline, resources.source_url,
                   resources.visible, users.name AS publisher, users.organization,
                   (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id) AS followers,
                   resources.created_at, resources.updated_at
            FROM resources JOIN users ON users.id = resources.owner_id WHERE 1 = 1
        """
        params = []
        if status == "active":
            query += " AND resources.visible = 1"
        elif status == "inactive":
            query += " AND resources.visible = 0"
        if category in {"internship", "course"}:
            query += " AND resources.category = ?"
            params.append(category)
        if search:
            query += " AND (resources.title LIKE ? OR users.name LIKE ? OR users.organization LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])
        query += " ORDER BY resources.updated_at DESC, resources.id DESC"
        headers = [
            "Title", "Category", "Description", "Location", "Deadline", "Source URL",
            "Visible", "Publisher", "Organization", "Followers", "Created", "Updated",
        ]
        rows = db.execute(query, params).fetchall()
        values = (
            (
                row["title"], row["category"], row["description"], row["location"],
                row["deadline"], row["source_url"], row["visible"], row["publisher"],
                row["organization"], row["followers"], row["created_at"], row["updated_at"],
            )
            for row in rows
        )

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in values:
        writer.writerow([safe_csv_cell(value) for value in row])
    filename = f"pathwise-{dataset}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def safe_csv_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


@app.post("/admin/accounts/<int:user_id>/toggle")
@admin_required
def admin_toggle_account(user_id):
    db = get_db()
    user = db.execute(
        "SELECT id, name, role, active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None:
        abort(404)
    with db:
        db.execute(
            "UPDATE users SET active = ? WHERE id = ?",
            (0 if user["active"] else 1, user_id),
        )
    action = "deactivated" if user["active"] else "reactivated"
    flash(f"{user['name']}'s account was {action}.", "success")
    return redirect(
        url_for(
            "admin_dashboard",
            q=request.args.get("q", ""),
            status=request.args.get("status", "all"),
            category=request.args.get("category", "all"),
        )
    )


@app.route("/admin/accounts/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_account(user_id):
    db = get_db()
    account = db.execute(
        "SELECT id, name, email, role, organization, active FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if account is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        organization = request.form.get("organization", "").strip()
        active = 1 if request.form.get("active") == "1" else 0
        if not name or not email:
            flash("Name and email are required.", "error")
            return render_template(
                "admin_account_form.html", account={**dict(account), **request.form}
            )
        if account["role"] == "company" and not organization:
            flash("Company accounts need an organization name.", "error")
            return render_template(
                "admin_account_form.html", account={**dict(account), **request.form}
            )
        try:
            with db:
                db.execute(
                    """
                    UPDATE users SET name = ?, email = ?, organization = ?, active = ?
                    WHERE id = ?
                    """,
                    (name, email, organization, active, user_id),
                )
        except sqlite3.IntegrityError:
            flash("That email address is already in use.", "error")
            return render_template(
                "admin_account_form.html", account={**dict(account), **request.form}
            )
        flash("Account details saved.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin_account_form.html", account=dict(account))


@app.post("/admin/accounts/<int:user_id>/delete")
@admin_required
def admin_delete_account(user_id):
    db = get_db()
    account = db.execute(
        "SELECT id, name FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if account is None:
        abort(404)
    with db:
        db.execute(
            "DELETE FROM notifications WHERE resource_id IN (SELECT id FROM resources WHERE owner_id = ?)",
            (user_id,),
        )
        db.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM resources WHERE owner_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    flash(f"{account['name']}'s account and associated data were deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/resources/<int:resource_id>/toggle")
@admin_required
def admin_toggle_resource(resource_id):
    db = get_db()
    resource = db.execute(
        "SELECT id, title, visible FROM resources WHERE id = ?",
        (resource_id,),
    ).fetchone()
    if resource is None:
        abort(404)
    with db:
        db.execute(
            "UPDATE resources SET visible = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (0 if resource["visible"] else 1, resource_id),
        )
    action = "hidden" if resource["visible"] else "made visible"
    flash(f"{resource['title']} was {action}.", "success")
    return redirect(
        url_for(
            "admin_dashboard",
            q=request.args.get("q", ""),
            status=request.args.get("status", "all"),
            category=request.args.get("category", "all"),
        )
    )


def admin_resource_form(resource=None, editing=False):
    companies = get_db().execute(
        "SELECT id, name, organization FROM users WHERE role = 'company' AND active = 1 ORDER BY organization, name"
    ).fetchall()
    return render_template(
        "resource_form.html",
        resource=resource or {},
        editing=editing,
        admin_mode=True,
        back_url=url_for("admin_dashboard"),
        companies=companies,
    )


@app.route("/admin/resources/new", methods=["GET", "POST"])
@admin_required
def admin_new_resource():
    if request.method == "POST":
        values = resource_form_values()
        try:
            owner_id = int(request.form.get("owner_id", ""))
        except ValueError:
            owner_id = 0
        owner = get_db().execute(
            "SELECT id FROM users WHERE id = ? AND role = 'company' AND active = 1",
            (owner_id,),
        ).fetchone()
        error = validate_resource(values)
        if not owner:
            error = "Choose an active company or educator as the publisher."
        if error:
            flash(error, "error")
            return admin_resource_form({**values, "owner_id": owner_id})
        with get_db():
            get_db().execute(
                """
                INSERT INTO resources
                    (owner_id, title, category, description, steps, source_url, location, deadline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_id, *values.values()),
            )
        flash("Opportunity created and published.", "success")
        return redirect(url_for("admin_dashboard"))
    return admin_resource_form()


@app.route("/admin/resources/<int:resource_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_resource(resource_id):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM resources WHERE id = ?",
        (resource_id,),
    ).fetchone()
    if existing is None:
        abort(404)

    if request.method == "POST":
        values = resource_form_values()
        try:
            owner_id = int(request.form.get("owner_id", ""))
        except ValueError:
            owner_id = 0
        owner = db.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'company' AND active = 1",
            (owner_id,),
        ).fetchone()
        error = validate_resource(values)
        if not owner:
            error = "Choose an active company or educator as the publisher."
        if error:
            flash(error, "error")
            return admin_resource_form({**values, "owner_id": owner_id}, editing=True)
        with db:
            db.execute(
                """
                UPDATE resources
                SET owner_id = ?, title = ?, category = ?, description = ?, steps = ?,
                    source_url = ?, location = ?, deadline = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (owner_id, *values.values(), resource_id),
            )
            db.execute(
                """
                INSERT INTO notifications (user_id, resource_id, message)
                SELECT user_id, ?, ? FROM subscriptions WHERE resource_id = ?
                """,
                (resource_id, f"{values['title']} has been updated.", resource_id),
            )
        flash("Opportunity updated. Subscribers have been notified.", "success")
        return redirect(url_for("admin_dashboard"))
    return admin_resource_form(dict(existing), editing=True)


@app.post("/admin/resources/<int:resource_id>/delete")
@admin_required
def admin_delete_resource(resource_id):
    db = get_db()
    resource = db.execute(
        "SELECT id, title FROM resources WHERE id = ?",
        (resource_id,),
    ).fetchone()
    if resource is None:
        abort(404)
    with db:
        db.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
    flash(f"{resource['title']} and its subscriptions were deleted.", "success")
    return redirect(url_for("admin_dashboard"))


def roadmap_form_context(roadmap=None, learning_text="", internship_text=""):
    return render_template(
        "roadmap_form.html",
        roadmap=roadmap or {},
        learning_text=learning_text,
        internship_text=internship_text,
    )


def save_roadmap_links(db, roadmap_id, links):
    db.execute("DELETE FROM roadmap_links WHERE roadmap_id = ?", (roadmap_id,))
    for kind in ("learning", "internship"):
        for order_index, link in enumerate(links[kind]):
            db.execute(
                """
                INSERT INTO roadmap_links
                    (roadmap_id, kind, title, provider, description, url, order_index)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    roadmap_id, kind, link["title"], link["provider"],
                    link["description"], link["url"], order_index,
                ),
            )


@app.route("/admin/roadmaps/new", methods=["GET", "POST"])
@admin_required
def admin_new_roadmap():
    if request.method == "POST":
        values = get_roadmap_form_values()
        try:
            links = get_roadmap_links_form_values()
            error = validate_roadmap(values)
        except ValueError as exc:
            links = {"learning": [], "internship": []}
            error = str(exc)
        if error:
            flash(error, "error")
            return roadmap_form_context(
                values,
                request.form.get("learning_links", ""),
                request.form.get("internship_links", ""),
            )
        slug = roadmap_slug(values["title"])
        try:
            with get_db() as db:
                cursor = db.execute(
                    """
                    INSERT INTO roadmaps
                        (slug, title, field, summary, description, level, duration, skills, steps, visible)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slug, values["title"], values["field"], values["summary"],
                        values["description"], values["level"], values["duration"],
                        values["skills"], values["steps"], values["visible"],
                    ),
                )
                save_roadmap_links(db, cursor.lastrowid, links)
        except sqlite3.IntegrityError:
            flash("A roadmap with that title already exists. Edit the existing roadmap instead.", "error")
            return roadmap_form_context(
                values,
                request.form.get("learning_links", ""),
                request.form.get("internship_links", ""),
            )
        flash("Career roadmap created.", "success")
        return redirect(url_for("admin_dashboard"))
    return roadmap_form_context()


@app.route("/admin/roadmaps/<int:roadmap_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_roadmap(roadmap_id):
    db = get_db()
    existing = db.execute(
        "SELECT * FROM roadmaps WHERE id = ?", (roadmap_id,)
    ).fetchone()
    if existing is None:
        abort(404)
    if request.method == "POST":
        values = get_roadmap_form_values()
        try:
            links = get_roadmap_links_form_values()
            error = validate_roadmap(values)
        except ValueError as exc:
            links = {"learning": [], "internship": []}
            error = str(exc)
        if error:
            flash(error, "error")
            return roadmap_form_context(
                values,
                request.form.get("learning_links", ""),
                request.form.get("internship_links", ""),
            )
        slug = roadmap_slug(values["title"])
        try:
            with db:
                db.execute(
                    """
                    UPDATE roadmaps
                    SET slug = ?, title = ?, field = ?, summary = ?, description = ?,
                        level = ?, duration = ?, skills = ?, steps = ?, visible = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        slug, values["title"], values["field"], values["summary"],
                        values["description"], values["level"], values["duration"],
                        values["skills"], values["steps"], values["visible"], roadmap_id,
                    ),
                )
                save_roadmap_links(db, roadmap_id, links)
        except sqlite3.IntegrityError:
            flash("A roadmap with that title already exists.", "error")
            return roadmap_form_context(
                values,
                request.form.get("learning_links", ""),
                request.form.get("internship_links", ""),
            )
        flash("Career roadmap updated.", "success")
        return redirect(url_for("admin_dashboard"))

    links = db.execute(
        "SELECT * FROM roadmap_links WHERE roadmap_id = ? ORDER BY kind, order_index, id",
        (roadmap_id,),
    ).fetchall()
    learning = [link for link in links if link["kind"] == "learning"]
    internships = [link for link in links if link["kind"] == "internship"]
    return roadmap_form_context(
        dict(existing), roadmap_link_text(learning), roadmap_link_text(internships)
    )


@app.post("/admin/roadmaps/<int:roadmap_id>/toggle")
@admin_required
def admin_toggle_roadmap(roadmap_id):
    db = get_db()
    roadmap = db.execute(
        "SELECT id, title, visible FROM roadmaps WHERE id = ?", (roadmap_id,)
    ).fetchone()
    if roadmap is None:
        abort(404)
    with db:
        db.execute(
            "UPDATE roadmaps SET visible = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (0 if roadmap["visible"] else 1, roadmap_id),
        )
    flash(
        f"{roadmap['title']} was {'hidden' if roadmap['visible'] else 'made visible'}.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/roadmaps/<int:roadmap_id>/delete")
@admin_required
def admin_delete_roadmap(roadmap_id):
    db = get_db()
    roadmap = db.execute(
        "SELECT title FROM roadmaps WHERE id = ?", (roadmap_id,)
    ).fetchone()
    if roadmap is None:
        abort(404)
    with db:
        db.execute("DELETE FROM roadmaps WHERE id = ?", (roadmap_id,))
    flash(f"{roadmap['title']} roadmap and its resource links were deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        organization = request.form.get("organization", "").strip()
        if not name or not email or len(password) < 8 or role not in {"student", "company"}:
            flash("Enter your name and email, choose an account type, and use a password with at least 8 characters.", "error")
            return render_template("register.html")
        if role == "company" and not organization:
            flash("Please enter your company or organization name.", "error")
            return render_template("register.html")
        try:
            with get_db():
                cursor = get_db().execute(
                    "INSERT INTO users (name, email, password_hash, role, organization) VALUES (?, ?, ?, ?, ?)",
                    (name, email, generate_password_hash(password), role, organization),
                )
            session.clear()
            session["user_id"] = cursor.lastrowid
            session["csrf_token"] = secrets.token_urlsafe(32)
            flash("Your account is ready. Welcome to Pathwise!", "success")
            return redirect(url_for("dashboard"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect, or the account is inactive.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "all")
    status = request.args.get("status", "all")
    if g.user["role"] == "company":
        company_stats = db.execute(
            """
            SELECT COUNT(*) AS listing_count,
                   COALESCE(SUM(
                       (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id)
                   ), 0) AS follower_count
            FROM resources WHERE owner_id = ? AND visible = 1
            """,
            (g.user["id"],),
        ).fetchone()
        query = """
            SELECT resources.*,
                   (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id) AS subscriber_count
            FROM resources WHERE owner_id = ?
        """
        params = [g.user["id"]]
        if category in {"internship", "course"}:
            query += " AND category = ?"
            params.append(category)
        if status in {"active", "inactive"}:
            query += " AND visible = ?"
            params.append(1 if status == "active" else 0)
        if search:
            query += " AND (title LIKE ? OR description LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term])
        query += " ORDER BY updated_at DESC"
        resources = db.execute(query, params).fetchall()
        return render_template(
            "dashboard.html", resources=resources, subscriptions=[], notifications=[],
            company_stats=company_stats,
            search=search, category=category, status=status,
        )

    subscription_query = """
        SELECT resources.*, users.organization,
               (SELECT COUNT(*) FROM subscriptions WHERE resource_id = resources.id) AS subscriber_count
        FROM subscriptions JOIN resources ON resources.id = subscriptions.resource_id
        JOIN users ON users.id = resources.owner_id
        WHERE subscriptions.user_id = ? AND users.active = 1 AND resources.visible = 1
    """
    subscription_params = [g.user["id"]]
    if category in {"internship", "course"}:
        subscription_query += " AND resources.category = ?"
        subscription_params.append(category)
    if search:
        subscription_query += " AND (resources.title LIKE ? OR resources.description LIKE ? OR users.organization LIKE ?)"
        term = f"%{search}%"
        subscription_params.extend([term, term, term])
    subscription_query += " ORDER BY subscriptions.created_at DESC"
    subscriptions = db.execute(subscription_query, subscription_params).fetchall()
    notifications = db.execute(
        """
        SELECT notifications.*, resources.title, resources.category
        FROM notifications JOIN resources ON resources.id = notifications.resource_id
        WHERE notifications.user_id = ? AND resources.visible = 1
        ORDER BY notifications.created_at DESC LIMIT 8
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template(
        "dashboard.html", resources=[], subscriptions=subscriptions, notifications=notifications,
        search=search, category=category, status=status,
    )


@app.route("/resources/new", methods=["GET", "POST"])
@role_required("company")
def new_resource():
    if request.method == "POST":
        values = resource_form_values()
        error = validate_resource(values)
        if error:
            flash(error, "error")
            return render_template("resource_form.html", resource=values, editing=False)
        with get_db():
            get_db().execute(
                """
                INSERT INTO resources (owner_id, title, category, description, steps, source_url, location, deadline)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (g.user["id"], *values.values()),
            )
        flash("Your opportunity is live.", "success")
        return redirect(url_for("dashboard"))
    return render_template("resource_form.html", resource={}, editing=False)


@app.route("/resources/<int:resource_id>/edit", methods=["GET", "POST"])
@role_required("company")
def edit_resource(resource_id):
    resource = get_resource(resource_id, include_hidden=True)
    if resource["owner_id"] != g.user["id"]:
        abort(403)
    if request.method == "POST":
        values = resource_form_values()
        error = validate_resource(values)
        if error:
            flash(error, "error")
            return render_template("resource_form.html", resource=values, editing=True)
        with get_db():
            get_db().execute(
                """
                UPDATE resources
                SET title = ?, category = ?, description = ?, steps = ?, source_url = ?,
                    location = ?, deadline = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND owner_id = ?
                """,
                (*values.values(), resource_id, g.user["id"]),
            )
            get_db().execute(
                """
                INSERT INTO notifications (user_id, resource_id, message)
                SELECT user_id, ?, ?
                FROM subscriptions WHERE resource_id = ?
                """,
                (resource_id, f"{values['title']} has been updated.", resource_id),
            )
        flash("Changes saved. Your subscribers have been notified.", "success")
        return redirect(url_for("resource_detail", resource_id=resource_id))
    return render_template("resource_form.html", resource=dict(resource), editing=True)


def resource_form_values():
    return {
        "title": request.form.get("title", "").strip(),
        "category": request.form.get("category", "").strip(),
        "description": request.form.get("description", "").strip(),
        "steps": request.form.get("steps", "").strip(),
        "source_url": request.form.get("source_url", "").strip(),
        "location": request.form.get("location", "").strip(),
        "deadline": request.form.get("deadline", "").strip(),
    }


def validate_resource(values):
    if not values["title"] or not values["description"]:
        return "Add a title and description before publishing."
    if values["category"] not in {"internship", "course"}:
        return "Choose either internships or online courses."
    if not valid_http_url(values["source_url"]):
        return "Source link must be a valid http:// or https:// URL."
    return None


@app.route("/resources/<int:resource_id>")
def resource_detail(resource_id):
    resource = get_resource(resource_id)
    subscribed = False
    if g.user and g.user["role"] == "student":
        subscribed = get_db().execute(
            "SELECT 1 FROM subscriptions WHERE user_id = ? AND resource_id = ?",
            (g.user["id"], resource_id),
        ).fetchone() is not None
    return render_template("resource_detail.html", resource=resource, subscribed=subscribed)


@app.post("/resources/<int:resource_id>/subscribe")
@role_required("student")
def subscribe(resource_id):
    resource = get_resource(resource_id)
    with get_db():
        get_db().execute(
            "INSERT OR IGNORE INTO subscriptions (user_id, resource_id) VALUES (?, ?)",
            (g.user["id"], resource_id),
        )
    flash(f"You are now following updates for {resource['title']}.", "success")
    return redirect(url_for("resource_detail", resource_id=resource_id))


@app.post("/resources/<int:resource_id>/unsubscribe")
@role_required("student")
def unsubscribe(resource_id):
    get_resource(resource_id)
    with get_db():
        get_db().execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND resource_id = ?",
            (g.user["id"], resource_id),
        )
    flash("You have unfollowed this update.", "info")
    return redirect(url_for("resource_detail", resource_id=resource_id))


@app.post("/notifications/read")
@role_required("student")
def mark_notifications_read():
    with get_db():
        get_db().execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (g.user["id"],))
    flash("Notifications marked as read.", "success")
    return redirect(url_for("dashboard"))


@app.post("/account/deactivate")
@login_required
def deactivate_account():
    with get_db():
        get_db().execute("UPDATE users SET active = 0 WHERE id = ?", (g.user["id"],))
    session.clear()
    flash("Your account has been deactivated. Contact support if you want to restore access.", "info")
    return redirect(url_for("home"))


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request")), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You do not have permission to view this page."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="We couldn't find that page."), 404


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
