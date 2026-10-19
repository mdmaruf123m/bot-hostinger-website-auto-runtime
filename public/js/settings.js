function fillAccountForm(user) {
  if (!user) return;
  document.getElementById("settingsName").value = user.displayName || "";
  document.getElementById("settingsEmail").value = user.email || "";
}
window.addEventListener("panel-user-ready", (event) =>
  fillAccountForm(event.detail.user),
);
if (window.panelAuth.currentUser) fillAccountForm(window.panelAuth.currentUser);
document
  .getElementById("settingsForm")
  .addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.getElementById("settingsStatus");
    const user = window.panelAuth.currentUser;
    const name = document.getElementById("settingsName").value.trim();
    status.className = "settings-status";
    status.textContent = "Saving…";
    if (!user) {
      status.classList.add("error");
      status.textContent = "Your sign-in has expired. Please log in again.";
      return;
    }
    try {
      await user.updateProfile({ displayName: name });
      window.updateProfileCard(user);
      status.classList.add("success");
      status.textContent = "Your profile name has been updated.";
    } catch (error) {
      status.classList.add("error");
      status.textContent = error.message || "Could not save your changes.";
    }
  });
