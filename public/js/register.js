// Firebase registration page behavior.
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
  location.replace("/dashboard.html");
}
form.onsubmit = async (e) => {
  e.preventDefault();
  err.style.display = "none";
  btn.disabled = true;
  btn.textContent = "Creating account…";
  const name = document.getElementById("name").value.trim(),
    email = document.getElementById("email").value.trim(),
    password = document.getElementById("password").value;
  try {
    const c = await auth.createUserWithEmailAndPassword(email, password);
    await c.user.updateProfile({ displayName: name });
    await enter(c.user);
  } catch (x) {
    await auth.signOut().catch(() => {});
    show(x.message || "Registration failed");
    btn.disabled = false;
    btn.textContent = "Create account";
  }
};
