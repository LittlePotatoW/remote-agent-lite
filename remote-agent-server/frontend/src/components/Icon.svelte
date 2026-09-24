<script lang="ts">
  export let name: string;
  export let size = 20;
  export let stroke = 1.7;

  const icons: Record<string, string> = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    folder:
      '<path d="M3.4 7.6A2 2 0 0 1 5.4 5.6h3c.6 0 1.1.25 1.5.7l.7 1c.35.45.9.7 1.45.7h5.55a2 2 0 0 1 2 2v6.4a2 2 0 0 1-2 2H5.4a2 2 0 0 1-2-2z"/>',
    file: '<path d="M6 3.6h5.6L18 10v10.4a1.6 1.6 0 0 1-1.6 1.6H6a1.6 1.6 0 0 1-1.6-1.6V5.2A1.6 1.6 0 0 1 6 3.6z"/><path d="M11.6 3.6V10H18"/>',
    dots: '<circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none"/>',
    compose:
      '<path d="M20.5 13.5V19a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 19V6A1.5 1.5 0 0 1 6 4.5h5.5"/><path d="M16.6 3.9a1.9 1.9 0 0 1 2.7 2.7l-7.1 7.1-3.1.7.7-3.1z"/>',
    back: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
    download: '<path d="M12 4v10M8 10.5l4 4 4-4M5 18.5h14"/>',
    upload:
      '<path d="M12 16V5.5M8 9.5l4-4 4 4M5 15.5v2.5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2.5"/>',
    archive:
      '<rect x="3.4" y="4.4" width="17.2" height="4.6" rx="1.4"/><path d="M5.3 9v9.3a1.7 1.7 0 0 0 1.7 1.7h10a1.7 1.7 0 0 0 1.7-1.7V9"/><path d="M10.2 13.2h3.6"/>',
    copy:
      '<rect x="9" y="9" width="11.5" height="11.5" rx="2.4"/><path d="M15.5 9V5.9a2.4 2.4 0 0 0-2.4-2.4H5.9a2.4 2.4 0 0 0-2.4 2.4v7.2a2.4 2.4 0 0 0 2.4 2.4H9"/>',
    image:
      '<rect x="3.5" y="4.5" width="17" height="15" rx="2.4"/><circle cx="8.8" cy="9.6" r="1.7"/><path d="M4.2 16.6l4.3-4a1.7 1.7 0 0 1 2.3 0l3 2.9M13.6 14.4l1.9-1.8a1.7 1.7 0 0 1 2.3 0l2.3 2.2"/>',
    pin: '<path d="M9 4h6l-.8 4.2L17.5 12H6.5l3.3-3.8z"/><path d="M12 12v7"/>',
    clock: '<circle cx="12" cy="12" r="8.2"/><path d="M12 7.6V12l3.1 1.9"/>',
    trash:
      '<path d="M5.5 7.5h13M9.5 7.5V5.8a1.3 1.3 0 0 1 1.3-1.3h2.4a1.3 1.3 0 0 1 1.3 1.3v1.7"/><path d="M7.5 7.5l.8 11a1.5 1.5 0 0 0 1.5 1.4h4.4a1.5 1.5 0 0 0 1.5-1.4l.8-11"/>',
    pencil: '<path d="M4 20h4l10-10-4-4L4 16z"/><path d="M14.5 5.5l4 4"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    person: '<circle cx="12" cy="8.6" r="3.7"/><path d="M5 19.6c0-3.4 3.1-5.6 7-5.6s7 2.2 7 5.6"/>',
    send: '<path d="M12 19V6M6.5 11.5 12 6l5.5 5.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2.5" fill="currentColor" stroke="none"/>',
    terminal: '<path d="M6.5 8.5 10.5 12l-4 3.5"/><path d="M12.5 16h6"/>',
    chevronDown: '<path d="M6.5 9.5 12 15l5.5-5.5"/>',
    chevronRight: '<path d="M9.5 6.5 15 12l-5.5 5.5"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>'
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={stroke}
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  focusable="false"
>
  {@html icons[name] ?? ''}
</svg>
