## Portable Node.js and PHP runtimes

BotKeep container-এ Node.js/npm বা PHP CLI আগে থেকে না থাকলে, project detected হলে panel প্রথম Run-এর সময় official Node.js checksum manifest দিয়ে Node archive যাচাই করে এবং PHP CLI-এর portable binary download করে writable `runtime_bins/` cache-এ রাখে। Composer packages থাকলে Composer PHAR-ও তার published SHA-256 দিয়ে যাচাই করে download হয়। এর জন্য root/sudo লাগে না। প্রথম Run-এ internet থেকে runtime নামানোর কারণে সময় বেশি লাগতে পারে।

এটি কাজ করার জন্য BotKeep container-এ outbound HTTPS, writable storage, `tar`/`xz` archive support, এবং Linux x86_64 বা ARM64 architecture থাকতে হবে। Host network download block করলে, disk quota শেষ হলে, অথবা runtime architecture অন্য হলে BotKeep administrator-এর কাছ থেকে runtime-enabled image চাইতে হবে। PHP bot-এ দরকারি PHP extension portable build-এ থাকতে হবে; custom/unlisted extension থাকলে image-based setup প্রয়োজন।

# Cloudflare Pages দিয়ে User Panel UI deploy (বাংলা)

এই repository-তে Cloudflare Pages-এর জন্য static frontend আছে: আলাদা Login, Register, dashboard এবং `/backend/*` request-এর Pages Function proxy। GitHub-এ `main` push হলে Pages UI deploy করবে। Python/Node.js/PHP bot, subprocess, dependency install, logs ও uploaded files নির্বাচিত bot backend-এ থাকবে; Pages-এ bot process চালানো হয় না। Firebase Auth ও user profile-এর Firestore read/write browser থেকে সরাসরি Firebase-এ হয়—bot backend Firebase Identity Toolkit/Firestore profile API call করে না। Login-এর signed Firebase ID token শুধু session তৈরি করার জন্য নির্বাচিত backend-এ পাঠানো হয়; backend Google-এর public signing key দিয়ে locally verify করে।

## Pages প্রথমবার সেটআপ

1. Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git** নির্বাচন করুন।
2. GitHub repository `mdmaruf123m/bot-hostinger-website-auto-runtime` এবং production branch `main` সিলেক্ট করুন।
3. Build settings:
   - Framework preset: **None**
   - Build command: ফাঁকা রাখুন
   - Build output directory: `public`
   - Root directory: repository root
4. Pages project-এ static frontend-এর সঙ্গে `functions/backend/[[path]].js` deploy হয়েছে কি না নিশ্চিত করুন।
5. Private `admin.html` খুলে **Bot hosting backend**-এ আপনার Render/BotKeep/অন্য host-এর live HTTPS origin দিন, যেমন `https://your-service.onrender.com` বা BotKeep-এর HTTPS domain। শুধু origin দিন—path, query বা শেষে API route নয়। Save করলে এটি Firebase Firestore-এর `users/panel_config` document-এর `backendOrigin` field-এ থাকবে।
6. Pages Function প্রতিটি `/backend/*` request-এর জন্য ওই Firestore setting পড়ে এবং বেছে নেওয়া backend-এ proxy করে; সেটি অল্প সময় cache হয়। তাই backend পাল্টাতে code edit বা GitHub redeploy লাগবে না।
7. Firebase Console → Authentication → Settings → Authorized domains-এ আপনার Pages hostname যোগ করুন, যেমন `your-project.pages.dev`। Firestore-এ `users` collection-এর existing Firestore Rules দিয়ে `panel_config` document read ও private admin write করতে দিন; admin panel user list-এ এই system configuration document দেখাবে না।

## HTTPS backend আবশ্যক

BotKeep-এর plain HTTP port-এ Pages proxy পাঠানো যাবে না; backend origin অবশ্যই HTTPS হতে হবে। আপনার BotKeep service-কে HTTPS custom domain/tunnel দিন, অথবা অন্য HTTPS-capable backend (যেমন Render) deploy করে তার live origin admin panel-এ save করুন। শুধু Pages domain তৈরি করলে BotKeep backend HTTPS হয় না।

## সীমা ও আচরণ

- Cloudflare Free plan-এ Worker/Pages Function দিয়ে পাঠানো request body-এর সীমা সাধারণত 100 MB; তাই Pages দিয়ে ZIP upload 100 MB বা কম রাখুন। BotKeep Flask-এ সরাসরি upload ceiling 200 MB থাকলেও Cloudflare proxy-এর সীমাই কার্যকর হবে।
- `users/panel_config.backendOrigin` সঠিক HTTPS origin না থাকলে proxy 503 দেবে; backend unreachable হলে dashboard-এ error দেখা যাবে।
- Cloudflare Pages static UI ও proxy চালায়। Render/BotKeep/নির্বাচিত service-এ bot runner backend deploy থাকতে হবে; শুধু URL পাল্টালে bots/files এক host থেকে অন্য host-এ migrate বা copy হয় না।
- এই setup-এ Flask-এর signed session cookie HTTPS-এ `Secure`, `HttpOnly` এবং `SameSite=Lax` হিসেবে ব্যবহৃত হয়।

## নতুন Firebase project সেটআপ (`bot-36914`)

- Firebase Console → **Authentication → Sign-in method**-এ **Email/Password** provider চালু করুন।
- Authentication → **Settings → Authorized domains**-এ Pages-এর `*.pages.dev` hostname যোগ করুন।
- Firestore Database-এ login/register flow-এর জন্য signed-in user যেন নিজের `users/{uid}` profile read/write করতে পারে, সেটি নিশ্চিত করুন। Registration browser থেকেই profile তৈরি/সিঙ্ক করে; bot backend Firebase profile API চালায় না। Admin panel-এর বিদ্যমান user-management rules এবং `users/panel_config` setting document-এর read/write permission-ও বজায় রাখুন।
- private `admin.html` নতুন Firebase project-এর সঙ্গে যুক্ত করা হয়েছে, কিন্তু তার Firestore list/block/delete কার্যক্রম Security Rules-এর অনুমতির ওপর নির্ভর করবে। Client-side password gate Firestore Security Rules-এর বিকল্প নয়।


## Frontend page ও source file layout

Cloudflare Pages-এর build output directory `public` থাকবে (build command ফাঁকা)। Source-এ আলাদা `login.html`, `register.html`, `dashboard.html`, `plan.html` ও `settings.html` আছে; Pages-এর clean URL behavior অনুযায়ী browser routes extensionless—`/login`, `/register`, `/dashboard`, `/plan`, `/settings`। তাই internal links extensionless route ব্যবহার করে; `.html` URL-কে `.html`-এ ফেরত redirect করা হয় না। `public/index.html` root `/` থেকে `/dashboard`-এ পাঠায়। `/login/`, `/register/`, `/dashboard/`, `/settings/` ও পুরনো `/plans` path `_redirects` দিয়ে canonical extensionless route-এ যায়। Shared styles `public/css/app.css` ও `public/css/auth.css`-এ, আর Firebase initialization, navigation/auth guard, dashboard controls এবং page-specific behavior `public/js/`-এ ভাগ করা হয়েছে। Menu drawer-এর open/close transition দ্রুত করা হয়েছে (প্রায় 150 ms)।
