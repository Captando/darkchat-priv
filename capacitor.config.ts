import type { CapacitorConfig } from '@capacitor/cli';

const serverUrl = process.env.CAP_SERVER_URL || 'http://localhost:8000';

const config: CapacitorConfig = {
  appId: 'com.captando.darkchat',
  appName: 'Private DarkChat',
  webDir: 'dist',
  server: {
    url: serverUrl,
    cleartext: true
  }
};

export default config;
