import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

HTMLElement.prototype.scrollIntoView = () => undefined;
afterEach(() => { cleanup(); sessionStorage.clear(); });
