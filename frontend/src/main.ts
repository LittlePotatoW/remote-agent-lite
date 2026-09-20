import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';
import { applyTheme, readTheme } from './lib/theme';

applyTheme(readTheme());

const app = mount(App, {
  target: document.getElementById('app')!
});

export default app;
