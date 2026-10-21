import { mount } from 'svelte';
import '@fontsource-variable/archivo/standard.css';
import './app.css';
import App from './App.svelte';

mount(App, { target: document.getElementById('app') });
