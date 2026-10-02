// Applied before the first paint, so a remembered theme never flashes the other
// one on the way in. This is a file rather than an inline <script> because the
// CSP is `script-src 'self'` -- see app/security.py, and don't relax it for this.
(function () {
  try {
    var t = localStorage.getItem('mg-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) {
    // A private window can refuse localStorage. The system preference still applies.
  }
})();
