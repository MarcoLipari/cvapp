The app I made to help me easily and quickly create, format and tailor my cv/resume, and also track my job applications. Here's an AI generated README:

# CV Manager

A local macOS desktop app for tracking job applications and building tailored CV snapshots.

## Supported systems

The initial production target is **macOS 13 Ventura or newer on Apple silicon**. Intel Macs and iOS are not supported by the initial release. Release builds should be produced and tested as `arm64`; broader architecture support can be added after a separate Intel build and clean-device test pass are in place.

CV Manager stores its database, exports, backups, bridge files, and diagnostic logs locally on the Mac. On first launch, it asks for the personal details to use in new CVs; the source code contains no prefilled personal profile.

## Install and run

From the project directory, create a virtual environment and install the pinned dependency:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Then start the desktop app:

```bash
.venv/bin/python main.py
```

The app creates its local database and export folders on first launch. Use **Open data folder** in Personal Details to open their location in the macOS Application Support directory for CV Manager.

Diagnostic logs are stored in the app's `logs` folder under Application Support. Logs rotate automatically and retain up to five previous 1 MB files.

Use **Export full backup** in Personal Details to create a portable JSON copy of your profile, reusable entries, CV snapshots, their histories, and applications. Use **Export CSV** on Applications when you want to analyze or share the application tracker.

## CV workflow

1. Update **Personal Details** once. Changes apply to every current CV and to new CVs, while past CV history keeps its original details.
2. Build reusable entries in **Entry Library**. Give each one a descriptive library name, such as `Payments migration`, then choose an existing CV section such as `Projects` from the dropdown—or type a new section name.
3. Create a tailored CV by selecting and ordering those entries. Put entries with the same CV section next to each other and they are exported together under one heading, so projects, roles, and other content can be mixed and matched independently. Add comma-separated entry keywords to note where each entry fits; keywords help with selection and are not exported. Contact details are snapshotted, while library entries stay linked to their reusable content.
4. Track the application and link it to the exact CV sent.

Saved CVs can be edited from **Tailored CVs**. Add job keywords such as `backend, Python, platform engineering` to record which roles each version best suits; these appear in the CV list and details but not in the exported résumé. Editing starts with the saved entry content and order, lets you tailor wording without changing the reusable library, add current library entries, and regenerate the Markdown and PDF. Editing an entry's wording inside a CV detaches that block from future library updates. The CV keeps its original contact-detail snapshot.

Use **Document editor** for a page-like editing view of a saved CV. The Document View approximates the final typography and supports undo, redo, bold, italic, and bullet editing; **Markdown Source** exposes the same section content directly. These text edits preserve every existing Entry Library link. Repeated adjacent headings in the editor keep linked-entry boundaries explicit but are still grouped into one heading in the final output. Use Edit CV or Tree View to add or remove entries, and Personal Details to change the header. Saving passes the edited content through the existing PDF exporter, which remains authoritative for exact layout and page overflow, then refreshes the PDF available to the Safari extension.

Use **Tree View** when you want to customize the whole saved snapshot in one place. The CV is the root; personal details and entries sit beneath it; and each entry owns its lines and bullet points. Reordering whole entries changes only that CV and preserves their library links. Right-click any row inside a CV-specific entry and choose **Move to linked entries** to add it to the Entry Library and link the current CV when saved. Changes save and export when you leave Tree View, select another CV, or choose **Save & export**. For each linked entry whose heading, category, or content changed, the save flow asks you to **Create entry copy**—adding a reusable `CV name | entry name` copy to Entry Library and linking this CV to it—or **Edit shared entry (updates N CVs)** to update the original and every CV that still links to it. **Cancel** stops the save and keeps your unsaved tree edits in place.

In **Entry Library**, use the **CV section** menu to show all entries or only entries for a selected exported heading, such as Skills or Projects. When one imported library item contains multiple roles or projects, right-click the item or one of its sub-entries and choose **Split into separate entries**; linked CVs keep both new entries together in the same order. Right-click a sub-entry to delete it, or right-click a content row to add or delete a bullet or line. Right-click a bullet to move it up or down, or drag it to another position among the bullets in the same group.

Each Entry Library item has an internal library name and a separate CV section heading. Renaming the internal name keeps every CV link and does not change exported CVs. Inline edits are autosaved, including before duplicating an entry. Leaving Entry Library records the editing session as one history version, updates every linked CV, and regenerates their Markdown and PDF exports. If updated content no longer fits legibly on one page, the CV remains updated and the app reports which export needs attention.

Right-click a saved CV and choose **Open PDF** to open its current export, or choose **See history** to open any previous version as a PDF. Every PDF keeps the same professional filename based on your name, while CV-specific export folders prevent one tailored version from overwriting another. Historical PDFs are generated on demand in a separate history export folder, so they never replace the current CV's PDF. In **Entry Library**, right-click an entry or any of its content rows and choose **See history** to inspect an earlier version in a compact read-only preview. Exporting or regenerating a PDF does not create a content version, and viewing history never changes current content.

Select any application, CV, or library entry to see its complete details below the table, including notes and posting URLs, linked applications, saved contact information, export paths, and full entry content.

### Import an existing CV

In **Entry Library**, choose **Import CV** and select a PDF, Markdown, or text CV. The importer recognizes standard headings such as Education, Experience, Projects, and Skills; preserves bullets and links in entry titles and bullet text; and converts right-hand date/location columns into the app's export syntax. Review the detected content before saving it. Updating Personal Details is opt-in, so importing a CV does not overwrite your current profile by default.

### Section formatting

Use Markdown in a section:

```text
**Data Engineering Intern** :: *May 2026 - Present*
*Example Company* :: *Montreal, QC*
- Built reliable data pipelines with Python and SQL.
- Improved reporting for analytics stakeholders.
```

`left :: right` produces the right-aligned date/location column from the reference CV. `**bold**`, `*italic*`, and bullets are preserved in exported Markdown and PDF.

Use standard Markdown links for clickable blue, underlined text in the PDF:

```text
**[Project title](https://github.com/example/project)** :: *Jan 2026 - Present*
- Integrated [OpenLineage](https://github.com/OpenLineage/OpenLineage/tree/main) for data lineage.
```

The PDF exporter targets one US letter page at the standard reference sizes: 24 pt name, 10 pt contact details, 12 pt section headings, and 11 pt body text. If the selected content overflows, the app warns you and offers three choices: shrink the formatting to produce a one-page PDF, keep the standard sizes and flow onto additional Letter pages, or cancel and shorten the CV. After shrinking, the app reports the final body font size and the recommended 10-12 pt range for standard CVs.

## Safari integration

CV Manager publishes exported PDFs to a private App Group folder shared with its native Safari Web Extension. There is no localhost server, copied endpoint, or token. The extension can:

- show saved CVs beside résumé upload fields and attach one without browsing through Finder;
- match a manually uploaded PDF to its CV Manager record;
- detect likely successful application submissions and save the posting details and description;
- show an in-page **Job logged** notice with Edit and **Don't log** actions; and
- queue changes while the desktop app is closed.

Open **Safari Integration** in CV Manager to see the shared catalog and queued-request status. Captured records enter the tracker as **Applied** and retain the exact CV selected in Safari.

### Build the Safari extension

The web resources live in [`safari-extension`](safari-extension), and the native Swift message handler and entitlements live in [`safari-native`](safari-native). Full Xcode is required to convert and package the extension. Follow [`safari-native/README.md`](safari-native/README.md) once Xcode is installed.

The extension never sends CVs or job details to a CV Manager service. Native messages only read the shared CV catalog or append an application request to local App Group storage.

## Tests

Run the complete test suite from the project directory:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
