## Portable Node.js and PHP runtimes

BotKeep container-এ Node.js/npm বা PHP CLI আগে থেকে না থাকলে, project detected হলে panel প্রথম Run-এর সময় official Node.js checksum manifest দিয়ে Node archive যাচাই করে এবং PHP CLI-এর portable binary download করে writable `runtime_bins/` cache-এ রাখে। Composer packages থাকলে Composer PHAR-ও তার published SHA-256 দিয়ে যাচাই করে download হয়। এর জন্য root/sudo লাগে না। প্রথম Run-এ internet থেকে runtime নামানোর কারণে সময় বেশি লাগতে পারে।

এটি কাজ করার জন্য BotKeep container-এ outbound HTTPS, writable storage, `tar`/`xz` archive support, এবং Linux x86_64 বা ARM64 architecture থাকতে হবে। Host network download block করলে, disk quota শেষ হলে, অথবা runtime architecture অন্য হলে BotKeep administrator-এর কাছ থেকে runtime-enabled image চাইতে হবে। PHP bot-এ দরকারি PHP extension portable build-এ থাকতে হবে; custom/unlisted extension থাকলে image-based setup প্রয়োজন।

# Cloudflare Pages দিয়ে User Panel UI deploy (বাংলা)

এই repository-তে এখন Cloudflare Pages-এর জন্য static frontend আছে: আলাদা Login, Register, dashboard এবং `/backend/*` request-এর Pages Function proxy। GitHub-এ `main` branch push করলে Pages নিজে থেকে নতুন UI deploy করতে পারবে। Python/Node.js/PHP bot, subprocess, dependency install, logs ও uploaded files **BotKeep backend-এই থাকবে**—Cloudflare Pages-এ bot চালানো হবে না।

## Pages প্রথমবার সেটআপ

1. Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git** নির্বাচন করুন।
2. GitHub repository `mdmaruf123m/bot-hostinger-website-auto-runtime` এবং production branch `main` সিলেক্ট করুন।
3. Build settings:
   - Framework preset: **None**
   - Build command: ফাঁকা রাখুন
   - Build output directory: `public`
   - Root directory: repository root
4. প্রথম deploy সম্পন্ন হলে Pages project-এর **Settings → Variables and Secrets → Add variable**-এ এই variable দিন:
   - `BACKEND_ORIGIN` = BotKeep API-এর স্থায়ী **HTTPS origin**; শুধু origin, শেষে slash/path নয়। উদাহরণ: `https://api.example.com`
   - এটি secret key নয়, তবে এই origin-টি trusted BotKeep service-এ resolve হওয়া চাই।
5. variable যোগ/পরিবর্তনের পর নতুন deployment চালান। Pages Function `/backend/...` request BotKeep origin-এ proxy করবে এবং session cookie-ও Pages domain দিয়ে নিরাপদে প্রবাহিত হবে।
6. Firebase Console → Authentication → Settings → Authorized domains-এ আপনার Pages hostname যোগ করুন, যেমন `your-project.pages.dev`। Firestore rules আগের মতোই রাখুন; registration `users/{uid}` profile লেখে।

## HTTPS backend আবশ্যক

বর্তমান BotKeep ঠিকানা `node5.botkeep.cloud:41198`-এ plain HTTP সাড়া দিয়েছে; এই origin-এ browser/session ব্যবহার নিরাপদ নয় এবং Pages-এর `BACKEND_ORIGIN` validator HTTP প্রত্যাখ্যান করবে। BotKeep runner-এর সামনে একটি স্থায়ী HTTPS endpoint/tunnel প্রয়োজন; HTTPS ঠিকমতো কাজ ও যাচাই হওয়ার পর সেই host-টি `BACKEND_ORIGIN`-এ বসাতে হবে। BotKeep-এর `SERVER_PORT=41198` অপরিবর্তিত থাকবে। BotKeep যে HTTPS custom-domain/tunnel পদ্ধতি সমর্থন করে সেটি ব্যবহার করুন; শুধু Pages domain তৈরি করলেই BotKeep backend HTTPS হয়ে যায় না।

## সীমা ও আচরণ

- Cloudflare Free plan-এ Worker/Pages Function দিয়ে পাঠানো request body-এর সীমা সাধারণত 100 MB; তাই Pages দিয়ে ZIP upload 100 MB বা কম রাখুন। BotKeep Flask-এ সরাসরি upload ceiling 200 MB থাকলেও Cloudflare proxy-এর সীমাই কার্যকর হবে।
- `BACKEND_ORIGIN` সঠিক ও অনলাইন না থাকলে dashboard দেখাবে যে BotKeep backend অনুপলব্ধ।
- Cloudflare Pages শুধুমাত্র static UI ও proxy চালায়। BotKeep বন্ধ থাকলে বা তার persistent storage/processes না থাকলে bot চলবে না।
- এই setup-এ Flask-এর signed session cookie HTTPS-এ `Secure`, `HttpOnly` এবং `SameSite=Lax` হিসেবে ব্যবহৃত হয়।

## নতুন Firebase project সেটআপ (`bot-36914`)

- Firebase Console → **Authentication → Sign-in method**-এ **Email/Password** provider চালু করুন।
- Authentication → **Settings → Authorized domains**-এ Pages-এর `*.pages.dev` hostname যোগ করুন।
- Firestore Database-এ `users/{uid}` profile পড়া/লেখার জন্য owner-only rule রাখুন, যেমন `request.auth != null && request.auth.uid == uid`। Registration backend session handoff-এর সময় profile তৈরি/সিঙ্ক করে; client থেকে আলাদা duplicate write করা হয় না।
- private `admin.html` নতুন Firebase project-এর সঙ্গে যুক্ত করা হয়েছে, কিন্তু তার Firestore list/block/delete কার্যক্রম Security Rules-এর অনুমতির ওপর নির্ভর করবে। Client-side password gate Firestore Security Rules-এর বিকল্প নয়।
