# CV Manager

A local macOS desktop app for tracking job applications and building tailored CV snapshots.

## Supported systems

The initial production target is **macOS 13 Ventura or newer on Apple silicon**. Intel Macs and iOS are not supported by the initial release. Release builds should be produced and tested as `arm64`; broader architecture support can be added after a separate Intel build and clean-device test pass are in place.

CV Manager stores its database, exports, backups, bridge files, and diagnostic logs locally on the Mac. On first launch, it asks for the personal details to use in new CVs; the source code contains no prefilled personal profile.

## Run

```bash
.venv/bin/python main.py
```

Your database and exports are stored in the macOS Application Support directory for CV Manager.

Diagnostic logs are stored in the app's `logs` folder under Application Support. Logs rotate automatically and retain up to five previous 1 MB files.

Use **Export full backup** in Personal Details to create a portable JSON copy of your profile, reusable sections, CV snapshots, their histories, and applications. Use **Export CSV** on Applications when you want to analyze or share the application tracker.

## CV workflow

1. Update **Personal Details** once. These details are copied into each new CV.
2. Build reusable blocks in **Section Library**.
3. Create a tailored CV by selecting and ordering the blocks. Add comma-separated section keywords to library sections to note where each block fits; keywords help with selection and are not exported. Contact details are snapshotted, while library-sourced sections stay linked to their reusable content.
4. Track the application and link it to the exact CV sent.

Saved CVs can be edited from **Tailored CVs**. Add job keywords such as `backend, Python, platform engineering` to record which roles each version best suits; these appear in the CV list and details but not in the exported résumé. Editing starts with the saved section content and order, lets you tailor wording without changing the reusable library, add current library sections, and regenerate the Markdown and PDF. Editing a section's wording inside a CV detaches that block from future library updates. The CV keeps its original contact-detail snapshot.

Use **Tree View** when you want to customize the whole saved snapshot in one place. The CV is the root; personal details and sections sit beneath it; and each section owns its entry lines and bullet points. Reordering whole sections changes only that CV and preserves their library links. Changes save and export when you leave Tree View, select another CV, or choose **Save & export**. For each linked section whose title, category, or content changed, the save flow asks you to **Create section copy**—adding a reusable `CV name | section name` copy to Section Library and linking this CV to it—or **Edit shared section (updates N CVs)** to update the original and every CV that still links to it. **Cancel** stops the save and keeps your unsaved tree edits in place.

In **Section Library**, use the **CV heading** menu to show all sections or only sections with a selected exported heading, such as Skills or Experience. Right-click an entry or content row to add a bullet. Right-click a bullet to move it up or down, or drag it to another position among the bullets in the same group.

Each Section Library item has an internal library name and a separate CV heading. Renaming the internal name keeps every CV link and does not change exported CVs. Inline edits are autosaved, including before duplicating a section. Leaving Section Library records the editing session as one history version, updates every linked CV, and regenerates their Markdown and PDF exports. If updated content no longer fits legibly on one page, the CV remains updated and the app reports which export needs attention.

Right-click a saved CV and choose **See history** to open any previous version as a PDF. Historical PDFs are generated on demand in a separate history export folder, so they never replace the current CV's PDF. In **Section Library**, right-click a section or any of its content rows and choose **See history** to inspect an earlier version in a compact read-only preview. Exporting or regenerating a PDF does not create a content version, and viewing history never changes current content.

Select any application, CV, or library section to see its complete details below the table, including notes and posting URLs, linked applications, saved contact information, export paths, and full section content.

### Import an existing CV

In **Section Library**, choose **Import CV** and select a PDF, Markdown, or text CV. The importer recognizes standard headings such as Education, Experience, Projects, and Skills; preserves bullets and links in entry titles and bullet text; and converts right-hand date/location columns into the app's export syntax. Review the detected sections before saving them. Updating Personal Details is opt-in, so importing a CV does not overwrite your current profile by default.

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

The PDF exporter always targets one US letter page. It uses the normal reference styling first, then progressively tightens spacing and type only when the selected content would overflow. If the content would become unreadable, export stops and asks you to shorten a section rather than generating a second page.

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
