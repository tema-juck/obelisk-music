# Obelisk Radio design

Radio reuses the established midnight/mono themes and minimalist terminal grid.
Runtime Theme definitions in src/obelisk_radio/app.py own the palette; radio.tcss
uses semantic variables only. NavigationTable owns row selection and j/k. PlainText
renders all station/ICY metadata without markup. Input owns typing. RadioScreen
emits intents; RadioController owns workers and I/O. Help uses native focus restore.

Verify 120×35, 80×24, 60×20 and 40×12 in both themes, including empty and slow
requests. Favorites are the offline startup view. Source switching and Spotify
controls do not exist. Enter in a field applies filters without starting audio;
Space in results stops/resumes at the live edge. Metadata is never invented.
