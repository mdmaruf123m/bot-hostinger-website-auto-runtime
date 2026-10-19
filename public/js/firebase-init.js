// Firebase Web SDK setup shared by every page. The Web config is public client configuration.
const firebaseConfig = {
  apiKey: "AIzaSyCaMi91Dp6s7ydrKMO4DwfWcJXMaViG_d4",
  authDomain: "bot-36914.firebaseapp.com",
  projectId: "bot-36914",
  storageBucket: "bot-36914.firebasestorage.app",
  messagingSenderId: "365558992881",
  appId: "1:365558992881:web:cb801e341311d54685be50",
};
if (!firebase.apps.length) firebase.initializeApp(firebaseConfig);
window.panelAuth = firebase.auth();
window.panelDb = firebase.firestore();
