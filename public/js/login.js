// Firebase login page behavior.
const auth = window.panelAuth,
  db = window.panelDb,
  form = document.getElementById("form"),
  btn = document.getElementById("submit"),
  err = document.getElementById("error");
auth.setPersistence(firebase.auth.Auth.Persistence.SESSION).catch(() => {});
function show(s) {
  err.textContent = s;
  err.style.display = "block";
}
async function enter(user) {
  const ref = db.collection("users").doc(user.uid);
  const snap = await ref.get();
  let profile = snap.exists ? snap.data() : null;
  if (
    profile &&
    ["blocked", "disabled"].includes(
      String(profile.status || "Active").toLowerCase(),
    )
  )
    throw Error("This account is blocked. Contact the administrator.");
  if (!profile) {
    profile = {
      uid: user.uid,
      email: user.email || "",
      name: user.displayName || (user.email || "").split("@")[0],
      status: "Active",
      createdAt: Date.now(),
    };
    await ref.set(profile);
  }
  location.replace("/dashboard");
}
form.onsubmit = async (e) => {
  e.preventDefault();
  err.style.display = "none";
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const c = await auth.signInWithEmailAndPassword(
      document.getElementById("email").value.trim(),
      document.getElementById("password").value,
    );
    await enter(c.user);
  } catch (x) {
    await auth.signOut().catch(() => {});
    show(x.message || "Login failed");
    btn.disabled = false;
    btn.textContent = "Log in";
  }
};

async function enterImpersonatedUser() {
  const match = location.hash.match(/(?:^|&)impersonation_token=([^&]+)/);
  if (!match) return false;
  history.replaceState(null, '', location.pathname + location.search);
  try {
    const credential = await auth.signInWithCustomToken(decodeURIComponent(match[1]));
    await enter(credential.user);
    return true;
  } catch (error) {
    show(error.message || 'Support session could not be started.');
    await auth.signOut().catch(() => {});
    return true;
  }
}
enterImpersonatedUser();
