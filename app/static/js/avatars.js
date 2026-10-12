(function () {
    var PALETA = [
        '#1e40af', '#0f766e', '#b45309', '#7c3aed', '#be185d',
        '#4d7c0f', '#0369a1', '#c2410c', '#334155', '#9333ea'
    ];
    function color(nombre) {
        var n = 0;
        for (var i = 0; i < nombre.length; i++) {
            n = (n + nombre.charCodeAt(i)) % PALETA.length;
        }
        return PALETA[n];
    }
    document.querySelectorAll('[data-avatar-name]').forEach(function (el) {
        el.style.background = color(el.getAttribute('data-avatar-name') || '?');
    });
})();