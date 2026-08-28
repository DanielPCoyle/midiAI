#!/usr/bin/env node
// The app's gate. python has push_cc.py --selftest and term.py; the JS half
// had nothing, so a broken component was only found by opening the app.
//
//     node app/check.js
//
// Parses and transforms every source file. It will not catch a wrong colour
// or a bad layout -- it catches the thing that actually kept happening, which
// is a file that does not compile at all.
//
// Uses the jsx plugins directly rather than babel-preset-expo or
// @babel/preset-react: neither preset package is installed here, only the
// individual plugins, and a gate that cannot run is not a gate.
const babel = require('@babel/core');
const fs = require('fs');
const path = require('path');

const app = __dirname;
const files = [
  'App.js',
  ...fs
    .readdirSync(path.join(app, 'src'))
    .filter((f) => f.endsWith('.js'))
    .map((f) => path.join('src', f)),
];

let bad = 0;
for (const f of files) {
  try {
    babel.transformFileSync(path.join(app, f), {
      // resolved from this file, not the cwd -- node_modules lives beside it,
      // and the gate has to give the same answer from the repo root
      plugins: [
        require.resolve('@babel/plugin-syntax-jsx'),
        require.resolve('@babel/plugin-transform-react-jsx'),
      ],
      configFile: false,
      babelrc: false,
    });
    console.log(`ok    ${f}`);
  } catch (e) {
    bad += 1;
    console.log(`FAIL  ${f}\n      ${String(e.message).split('\n')[0]}`);
  }
}

// every component the app renders has to be reachable from App.js, and an
// import that resolves to nothing is the other failure that only shows up
// when you open it
for (const f of files) {
  const src = fs.readFileSync(path.join(app, f), 'utf8');
  for (const m of src.matchAll(/from\s+'(\.\/[^']+)'/g)) {
    const rel = m[1].endsWith('.js') ? m[1] : `${m[1]}.js`;
    const target = path.resolve(path.dirname(path.join(app, f)), rel);
    if (!fs.existsSync(target)) {
      bad += 1;
      console.log(`FAIL  ${f} imports ${m[1]}, which is not there`);
    }
  }
}

console.log(bad ? `\nFAIL (${bad})` : '\nOVERALL: PASS');
process.exit(bad ? 1 : 0);
