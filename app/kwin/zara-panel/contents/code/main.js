// Zara: mantiene la ventana del chat pegada al orbe.
const ORB = "Zara";
const PANEL = "Zara · Chat";
const GAP = 12;

function isZara(w) {
    return w && (w.resourceClass === "zara" || w.desktopFileName === "zara");
}
function isOrb(w) { return isZara(w) && w.caption === ORB; }
function isPanel(w) { return isZara(w) && w.caption.indexOf(PANEL) === 0; }

// El orbe del MISMO proceso que el chat (por si hubiera varias Zaras).
function findOrb(panel) {
    const ws = workspace.windowList();
    let any = null;
    for (let i = 0; i < ws.length; i++) {
        if (!isOrb(ws[i])) continue;
        if (panel && ws[i].pid === panel.pid) return ws[i];
        if (!any) any = ws[i];
    }
    return any;
}

function place(panel) {
    const orb = findOrb(panel);
    if (!orb || !panel) return;
    const o = orb.frameGeometry, p = panel.frameGeometry;
    if (p.width <= 0 || p.height <= 0) return;
    const a = workspace.clientArea(KWin.MaximizeArea, orb);
    // A la izquierda del orbe; si no cabe, a la derecha.
    let x = o.x - p.width - GAP;
    if (x < a.x) x = o.x + o.width + GAP;
    if (x + p.width > a.x + a.width) x = a.x + a.width - p.width;
    // Alineado con la parte superior del orbe, sin salirse de la pantalla.
    let y = o.y;
    if (y + p.height > a.y + a.height) y = a.y + a.height - p.height;
    if (y < a.y) y = a.y;
    panel.frameGeometry = { x: x, y: y, width: p.width, height: p.height };
}

function hook(w) {
    if (isPanel(w)) {
        place(w);
        // El tamaño final puede llegar un instante después del mapeo.
        w.frameGeometryChanged.connect(function () {
            if (!w.move) place(w);
        });
    } else if (isOrb(w)) {
        const orbPid = w.pid;
        const follow = function () {
            const ws = workspace.windowList();
            for (let i = 0; i < ws.length; i++)
                if (isPanel(ws[i]) && ws[i].pid === orbPid) place(ws[i]);
        };
        w.frameGeometryChanged.connect(follow);
        follow();
    }
}

workspace.windowAdded.connect(hook);
const existing = workspace.windowList();
for (let i = 0; i < existing.length; i++) hook(existing[i]);
