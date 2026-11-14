const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('get-app-version'),
  minimize: () => ipcRenderer.invoke('minimize-window'),
  maximize: () => ipcRenderer.invoke('maximize-window'),
  close: () => ipcRenderer.invoke('close-window')
});

// Expose a safe fetch wrapper for the local server
contextBridge.exposeInMainWorld('api', {
  get: async (endpoint) => {
    const response = await fetch(`http://127.0.0.1:8765${endpoint}`);
    return response.json();
  },
  post: async (endpoint, data) => {
    const response = await fetch(`http://127.0.0.1:8765${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return response.json();
  }
});
