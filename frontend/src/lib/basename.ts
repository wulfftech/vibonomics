/** The router basename: the path of `<base href>` (the backend's URL base), without a trailing slash. */
export function routerBasename(baseURI: string = document.baseURI): string {
  try {
    const path = new URL(baseURI).pathname.replace(/\/+$/, '');
    return path === '' ? '/' : path;
  } catch {
    return '/';
  }
}
