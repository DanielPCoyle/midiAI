import Constants from 'expo-constants';
import { Platform } from 'react-native';

export const PORT = 8765;

// Expo Go loads this bundle from the Mac over the network, so the Mac's
// address is already known here -- there is nothing for anyone to type into
// an iPad. On the web build the page came from the same machine.
export function defaultHost() {
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    return window.location.hostname || 'localhost';
  }
  const uri =
    Constants.expoConfig?.hostUri ||
    Constants.expoGoConfig?.debuggerHost ||
    Constants.manifest2?.extra?.expoGo?.debuggerHost ||
    '';
  return uri.split(':')[0] || 'localhost';
}

export const baseFor = (host) => `http://${host}:${PORT}`;

async function ask(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res;
}

export const getJSON = async (base, path) =>
  (await ask(`${base}${path}`)).json();

export const getText = async (base, path) => (await ask(`${base}${path}`)).text();

export const post = async (base, path, body) =>
  (await ask(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })).text();
