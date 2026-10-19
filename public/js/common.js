// Shared protected-page navigation, profile card, and Firebase auth guard.
(() => {
  const auth = window.panelAuth;
  const mount = document.getElementById("accountMenuMount");
  const svg = {
    brand:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 9 5v10l-9 5-9-5V7l9-5Z"/><path d="m3.5 7.2 8.5 4.9 8.5-4.9M12 12v10"/></svg>',
    dashboard:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10Z"/></svg>',
    plans:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5M12 13v8"/></svg>',
    settings:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="7" cy="18" r="2"/></svg>',
    logout:
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></svg>',
    chevron:
      '<svg class="menu-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>',
  };
  if (mount) {
    mount.innerHTML = `<header class="bar"><span class="brand-left"><span class="brand-glyph">${svg.brand}</span><b id="userName">Loading…</b></span><button class="menu-toggle" id="menuBtn" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="accountDrawer"><span></span><span></span><span></span></button></header><div class="drawer-backdrop" id="menuOverlay" hidden><aside class="account-drawer" id="accountDrawer" aria-label="Account menu" aria-hidden="true"><div class="drawer-head"><span class="drawer-kicker">MY ACCOUNT</span><button class="drawer-close" id="menuClose" type="button" aria-label="Close menu">&times;</button></div><section class="profile-card"><div class="profile-avatar" id="profileAvatar">U</div><div class="profile-copy"><b id="profileName">Loading…</b><span id="profileEmail">Loading account</span></div><span class="profile-status"><i></i> Online</span></section><nav class="drawer-nav" aria-label="Main menu"><a class="menu-item" data-page="dashboard.html" href="/dashboard"><span class="menu-icon">${svg.dashboard}</span><span>Dashboard</span>${svg.chevron}</a><a class="menu-item" data-page="plan.html" href="/plan"><span class="menu-icon">${svg.plans}</span><span>Plans</span>${svg.chevron}</a><a class="menu-item" data-page="settings.html" href="/settings"><span class="menu-icon">${svg.settings}</span><span>Settings</span>${svg.chevron}</a></nav><div class="drawer-footer"><div class="drawer-divider"></div><button class="menu-signout" id="logout" type="button"><span class="menu-icon">${svg.logout}</span><span>Sign out</span></button><small>Secure account menu</small></div></aside></div>`;
    const current = location.pathname.split("/").pop() || "dashboard.html";
    mount
      .querySelectorAll(".menu-item")
      .forEach((item) =>
        item.classList.toggle(
          "active",
          item.dataset.page === current ||
            (current === "index.html" &&
              item.dataset.page === "dashboard.html"),
        ),
      );
    const overlay = document.getElementById("menuOverlay");
    const drawer = document.getElementById("accountDrawer");
    const toggle = document.getElementById("menuBtn");
    let closeTimer;
    function openMenu() {
      clearTimeout(closeTimer);
      overlay.hidden = false;
      document.body.classList.add("menu-open");
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", "Close menu");
      drawer.setAttribute("aria-hidden", "false");
      requestAnimationFrame(() => {
        overlay.classList.add("open");
        drawer.classList.add("open");
      });
      document.getElementById("menuClose").focus();
    }
    function closeMenu() {
      overlay.classList.remove("open");
      drawer.classList.remove("open");
      document.body.classList.remove("menu-open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open menu");
      drawer.setAttribute("aria-hidden", "true");
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => {
        overlay.hidden = true;
        toggle.focus();
      }, 155);
    }
    toggle.addEventListener("click", () =>
      toggle.getAttribute("aria-expanded") === "true"
        ? closeMenu()
        : openMenu(),
    );
    document.getElementById("menuClose").addEventListener("click", closeMenu);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true")
        closeMenu();
    });
    document.getElementById("logout").addEventListener("click", async () => {
      closeMenu();
      try {
        const controller = new AbortController();
        setTimeout(() => controller.abort(), 1800);
        await fetch("/backend/logout", {
          credentials: "same-origin",
          signal: controller.signal,
        });
      } catch (_) {}
      await auth.signOut().catch(() => {});
      location.replace("/login");
    });
  }
  window.updateProfileCard = (user) => {
    const label =
      user.displayName || (user.email || "").split("@")[0] || "Account";
    for (const id of ["userName", "profileName"]) {
      const el = document.getElementById(id);
      if (el) el.textContent = label;
    }
    const email = document.getElementById("profileEmail");
    if (email) email.textContent = user.email || "Firebase account";
    const avatar = document.getElementById("profileAvatar");
    if (avatar)
      avatar.textContent = label.trim().charAt(0).toUpperCase() || "U";
  };
  async function ensureDefaultFreePlan(user) {
    const db = window.panelDb;
    const userRef = db.collection("users").doc(user.uid);
    const profileSnap = await userRef.get();
    const profile = profileSnap.exists ? profileSnap.data() : {};
    if (profile.activePlanId) {
      const activePlanSnap = await db.collection("plans").doc(String(profile.activePlanId)).get();
      if (activePlanSnap.exists) {
        const activePlan = activePlanSnap.data();
        const currentRam = Number(activePlan.ramBytes) || (Number(activePlan.ramGiB) || 0) * 1024 ** 3;
        const currentStorage = Number(activePlan.storageBytes) || (Number(activePlan.storageGiB) || 0) * 1024 ** 3;
        const changed = Number(profile.maxBots) !== (Number(activePlan.maxBots) || 0)
          || Number(profile.ramBytes || 0) !== currentRam
          || Number(profile.storageBytes || 0) !== currentStorage
          || profile.activePlanName !== activePlan.name;
        if (changed) {
          await userRef.set({
            activePlanName: activePlan.name || profile.activePlanName || "Package",
            maxBots: Number(activePlan.maxBots) || 0,
            ramGiB: Number(activePlan.ramGiB) || currentRam / (1024 ** 3),
            storageGiB: Number(activePlan.storageGiB) || currentStorage / (1024 ** 3),
            ramBytes: currentRam,
            storageBytes: currentStorage,
            updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
          }, { merge: true });
        }
      }
      return;
    }
    if (profile.planId || profile.plan) return;
    const billingSnap = await db.collection("admin_config").doc("billing").get();
    const freeId = billingSnap.exists ? billingSnap.data().defaultFreePlanId : "";
    if (!freeId) return;
    const planSnap = await db.collection("plans").doc(freeId).get();
    if (!planSnap.exists) return;
    const plan = planSnap.data();
    const start = new Date();
    let expiry = null;
    const unit = String(plan.durationUnit || "lifetime").toLowerCase();
    const amount = Math.max(1, Math.floor(Number(plan.durationValue) || 1));
    if (unit === "days") { expiry = new Date(start); expiry.setDate(expiry.getDate() + amount); }
    else if (unit === "months") { expiry = new Date(start); expiry.setMonth(expiry.getMonth() + amount); }
    else if (unit === "years") { expiry = new Date(start); expiry.setFullYear(expiry.getFullYear() + amount); }
    await db.runTransaction(async (tx) => {
      const latest = await tx.get(userRef);
      const latestData = latest.exists ? latest.data() : {};
      if (latestData.activePlanId || latestData.planId || latestData.plan) return;
      tx.set(userRef, {
        activePlanId: freeId,
        activePlanName: plan.name || "Free package",
        planStatus: "active",
        planStartedAt: firebase.firestore.FieldValue.serverTimestamp(),
        planExpiresAt: expiry ? firebase.firestore.Timestamp.fromDate(expiry) : null,
        maxBots: Number(plan.maxBots) || 0,
        ramGiB: Number(plan.ramGiB) || 0,
        storageGiB: Number(plan.storageGiB) || 0,
        ramBytes: Number(plan.ramBytes) || (Number(plan.ramGiB) || 0) * 1024 ** 3,
        storageBytes: Number(plan.storageBytes) || (Number(plan.storageGiB) || 0) * 1024 ** 3,
        updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
      }, { merge: true });
    });
  }
  auth.onAuthStateChanged(async (user) => {
    if (!user) {
      location.replace("/login");
      return;
    }
    try {
      await ensureDefaultFreePlan(user);
    } catch (_) {
      // Billing setup or Firestore permissions should not block account access.
    }
    try {
      const latest = await window.panelDb.collection("users").doc(user.uid).get();
      window.panelProfile = latest.exists ? latest.data() : {};
    } catch (_) {
      window.panelProfile = {};
    }
    window.updateProfileCard(user);
    window.dispatchEvent(
      new CustomEvent("panel-user-ready", { detail: { user } }),
    );
  });
})();
