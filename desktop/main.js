// The midiAI desktop app: one window, and the services it needs underneath it.
//
// The browser was never the product -- it was the cheapest way to get pixels
// while the surface was being built. What a window buys is the rest of it: an
// icon in the Dock, no tab strip, no address bar to mistype, and a bundle that
// can be signed and handed to someone.
//
//   npm start          window against the running Metro (develop like before)
//   npm run dist       a .app, loading the exported build off disk
//
// The Python half runs the same way it does under midiAI.command. This process
// owns those children: quitting the window stops them, because two push_cc
// processes fight over the same MIDI port and a stray one is invisible.
const { app, BrowserWindow, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PY = path.join(ROOT, '.venv', 'bin', 'python');
const DEV = !app.isPackaged;
// Packaged, the repo is not next to us -- extraResources put the build here.
const EXPORT = DEV
  ? path.join(ROOT, 'app', 'dist', 'index.html')
  : path.join(process.resourcesPath, 'app', 'dist', 'index.html');

let win = null;
const kids = [];

// Only if one is not already up. midiAI.command starts the same two, and two
// push_cc processes fight over the one MIDI port while a second mapui cannot
// bind 8765 -- both fail quietly, which reads exactly like the app being
// broken. Adopting the running pair is right anyway: it is the same surface.
function running(script) {
  try {
    return spawnSync('pgrep', ['-f', script]).status === 0;
  } catch {
    return false;
  }
}

function serve(script, args) {
  if (!fs.existsSync(PY) || running(script)) return;
  const p = spawn(PY, [script, ...args], { cwd: ROOT, stdio: 'ignore' });
  p.on('error', () => {});                 // a dead backend is not a crash
  kids.push(p);
}

function makeWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    backgroundColor: '#0b0b0d',            // matches app.json, so no white flash
    titleBarStyle: 'hiddenInset',          // the app draws its own header
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  // External links go to the real browser -- a link that replaces the surface
  // with a web page leaves no way back.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  if (DEV) win.loadURL('http://localhost:8081');
  else if (fs.existsSync(EXPORT)) win.loadFile(EXPORT);
  else win.loadURL('data:text/html,<body style="background:%230b0b0d;color:%23e6e6e6;'
    + 'font:14px system-ui;padding:40px">No web build. Run '
    + '<code>npx expo export -p web</code> in app/ and repackage.</body>');
  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  serve('push_cc.py', []);
  serve('mapui.py', ['--lan']);
  makeWindow();
  // macOS: the Dock icon reopens rather than starting a second copy
  app.on('activate', () => { if (!win) makeWindow(); });
});

app.on('window-all-closed', () => app.quit());
// Children are ours; leaving one holding the Push is worse than a slow quit.
app.on('before-quit', () => kids.forEach((p) => { try { p.kill(); } catch {} }));
