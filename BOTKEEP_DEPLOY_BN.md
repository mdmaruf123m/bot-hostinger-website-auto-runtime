# BotKeep-এ BotHostPanel পরীক্ষা

এই repo-টি BotKeep-এর **Python application profile**-এ panel চালানোর জন্য সাজানো হয়েছে। এটি পরীক্ষামূলক setup—BotKeep-এর Python runtime-এ panel-এর child-process, `venv` creation এবং per-bot dependency installation অনুমোদিত কি না deploy করে যাচাই করতে হবে।

## GitHub থেকে deploy

1. BotKeep-এ নতুন **Python application** তৈরি করুন এবং GitHub-এ এই public repo `mdmaruf123m/bot-hostinger-website-auto-runtime` নির্বাচন করুন।
2. Repository root/project directory-তে `requirements.txt` আছে নিশ্চিত করুন।
3. Start command দিন: `python botkeep_start.py`। Launcher BotKeep-এর `SERVER_PORT` ব্যবহার করে, না থাকলে `PORT`, তারপর `8080`।
4. Python version হিসেবে BotKeep-এ বর্তমানে offered version থেকে Python 3.11 বা 3.12 বেছে নিন।
5. Environment/Secrets-এ এই values দিন। App startup-এর আগে `python-dotenv` দিয়ে BotKeep-এর project-root `.env` ফাইলও load করে:
   - `ALLOW_SIGNUP=false`
   - `ADMIN_PASS` = আপনার নিজের শক্তিশালী, অনন্য admin password
   - `SECRET_KEY` = দীর্ঘ, random secret
   - `DATA_DIR` = BotKeep-এর dashboard-এ যে writable persistent directory/volume path দেখায়, সেই exact path
6. Deploy করুন। Console-এ `venv`, `pip`, permission, package install বা storage error আছে কি না দেখুন। Assigned app URL-এ `/admin` খুলে admin login দিয়ে যাচাই করুন।

**গুরুত্বপূর্ণ:** BotKeep-এর persistent storage path dashboard-এ আলাদা করে দেখানো থাকলে সেটিই `DATA_DIR` হিসেবে ব্যবহার করুন। Path নিশ্চিত না হলে সেটি আন্দাজ করে দেবেন না। Persistent writable storage না থাকলে account database, uploaded bot files, logs ও dependency caches restart/redeploy-এর পরে হারাতে পারে, অথবা directory write permission না থাকলে app শুরুই নাও হতে পারে।

## Dependency auto-install পরীক্ষা

Panel চালু হওয়ার পর `BOTKEEP-Single-File-Probe.zip` নামে প্রকাশিত test ZIP-টি panel-এ upload করুন। ZIP-এ `main.py` আছে, কিন্তু `requirements.txt` নেই। Validate করে Run চাপুন। Logs-এ `Auto-detected Python packages from source imports: idna` এবং `BOTKEEP_SINGLE_FILE_OK` এলে import scan, bot-specific venv, pip install ও child process-এর Python path কাজ করেছে। একই app-কে আবার start করলে নিজের cached Python environment reuse হওয়া উচিত। অন্য app name/user-এর cache আলাদা হওয়া উচিত।

এই repository update-এ probe ZIP রাখা হয়নি; user bot ZIP-গুলোতে secret রাখবেন না।

## BotKeep-এ কী পরীক্ষা হবে না

এই deployment guide panel itself-কে Python profile-এ চালায়। Panel-এর Dockerfile BotKeep GitHub deploy-এ custom image হিসেবে ব্যবহৃত হবে—এমন ধারণা করবেন না। তাই Node.js/npm ও PHP/Composer executable আগে থেকে আছে ধরে নেওয়া যাবে না। BotKeep-এ Node/PHP user bot চালাতে হলে সেই runtime tools আছে কি না আলাদা করে যাচাই করুন। BotKeep-এর public docs PHP website runtime দেখায়, কিন্তু এই panel-এর PHP CLI worker চালানোর নিশ্চয়তা দেয় না।

BotKeep-এর Python নির্দেশনা dependencies manifest ও explicit startup command ব্যবহারের পরামর্শ দেয়; তাই panel-এর “manifest ছাড়া import scan করে pip install” কাজ করবে কি না নিশ্চিত নয়—উপরের probe-ই সেটি যাচাই করবে। OS package/root access প্রয়োজন এমন Python dependency নাও চলতে পারে।

## নিরাপত্তা ও data

`ALLOW_SIGNUP=false` রাখুন এবং admin password/secret source code, ZIP বা public repo-তে লিখবেন না। এই panel uploaded code-কে একই container ও OS user-এ child process হিসেবে চালায়; এটি untrusted code-এর জন্য নিরাপদ sandbox নয়। শুধু নিজের বা বিশ্বস্ত bot code দিয়ে পরীক্ষা করুন। Test-এর আগে প্রয়োজনীয় data-এর আলাদা copy রাখুন।
