const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('miaRuntime', Object.freeze({
  platform: process.platform,
  getDeviceIdentity: () => ipcRenderer.invoke('mia:device-identity'),
  signDeviceChallenge: (challenge) =>
    ipcRenderer.invoke('mia:sign-device-challenge', challenge),
  storeLicenseToken: (token) => ipcRenderer.invoke('mia:license-store', token),
}));
