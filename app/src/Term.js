// A real terminal in the app: xterm.js on one end, a pty running `tmux attach`
// on the other.
//
// The tab beside this one is a picture of the pane with a keyboard taped to
// it -- capture-pane strips the colour, polling rules out a cursor, and there
// is no mouse and no scrollback. This has all four, because it is not a
// drawing of a terminal, it is one.
//
// Web only, and that is not a limitation to apologise for: xterm.js needs a
// DOM, and Pane.js keeps the scraped view for the native build rather than
// showing an iPad an empty box.
import { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { C } from './theme';

// The bytes are base64 on the wire in both directions -- a terminal emits
// arbitrary bytes and SSE is line-oriented, so a raw newline in a payload
// would end the event early and cut a frame in half.
const decode = (b64) => {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
};
const encode = (text) => {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
};

export default function Term({ base, pane, theme }) {
  const host = useRef(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!host.current || !base || !pane) return undefined;
    let dead = false;
    let term;
    let source;
    let cleanupResize = () => {};

    (async () => {
      // imported here rather than at the top so the native build never
      // evaluates a module that reaches for `document`
      const { Terminal } = await import('@xterm/xterm');
      const { FitAddon } = await import('@xterm/addon-fit');
      await import('@xterm/xterm/css/xterm.css');
      if (dead) return;

      term = new Terminal({
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 12,
        cursorBlink: true,
        scrollback: 5000,
        theme,
        // tmux draws the whole screen; letting xterm also convert an ambiguous
        // width would put the box-drawing half a cell out
        allowProposedApi: true,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host.current);
      fit.fit();

      // One request at a time, everything typed meanwhile riding the next --
      // the same rule the scraped view needed, for the same reason: these are
      // concurrent POSTs and a terminal is a stream with exactly one order.
      let waiting = '';
      let inFlight = false;
      const drain = () => {
        if (inFlight || !waiting) return;
        const batch = waiting;
        waiting = '';
        inFlight = true;
        fetch(`${base}/pty/input`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pane, data: encode(batch) }),
        }).catch(() => {}).finally(() => { inFlight = false; drain(); });
      };
      term.onData((d) => { waiting += d; drain(); });

      const tell = () => {
        fit.fit();
        fetch(`${base}/pty/resize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pane, cols: term.cols, rows: term.rows }),
        }).catch(() => {});
      };
      // the pty is told the size, tmux sizes the grouped session to match, and
      // the user's own Terminal window is left alone -- see ptybridge
      const obs = new ResizeObserver(() => tell());
      obs.observe(host.current);
      cleanupResize = () => obs.disconnect();

      source = new EventSource(
        `${base}/pty/stream?pane=${encodeURIComponent(pane)}&cols=${term.cols}&rows=${term.rows}`);
      source.onmessage = (e) => term.write(decode(e.data));
      source.addEventListener('gone', () => {
        term.write('\r\n\x1b[2m— the pane closed —\x1b[0m\r\n');
        source.close();
      });
      source.onerror = () => setErr('lost the connection to the pane');
      term.focus();
    })().catch((e) => setErr(String(e?.message || e)));

    return () => {
      dead = true;
      cleanupResize();
      try { source?.close(); } catch {}
      try { term?.dispose(); } catch {}
    };
  }, [base, pane, theme]);

  return (
    <View style={{ flex: 1, minHeight: 260 }}>
      {!!err && <Text style={{ color: C.bad, fontSize: 12, paddingBottom: 6 }}>{err}</Text>}
      {/* a plain div under react-native-web: xterm measures and paints it
          itself, so nothing here may set a transform or an overflow on it */}
      <View ref={host} style={{ flex: 1, minHeight: 240, overflow: 'hidden' }} />
    </View>
  );
}
