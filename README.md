# CV Manager

A local macOS desktop app for tracking job applications and building tailored CV snapshots.

## Run

```bash
.venv/bin/python main.py
```

Your database and exports are stored in the macOS Application Support directory for CV Manager.

Use **Export full backup** in Personal Details to create a portable JSON copy of your profile, reusable sections, CV snapshots, and applications. Use **Export CSV** on Applications when you want to analyze or share the application tracker.

## CV workflow

1. Update **Personal Details** once. These details are copied into each new CV.
2. Build reusable blocks in **Section Library**.
3. Create a tailored CV by selecting and ordering the blocks. Add comma-separated job labels to library sections to note where each block fits; labels help with selection and are not exported. Contact details are snapshotted, while library-sourced sections stay linked to their reusable content.
4. Track the application and link it to the exact CV sent.

Saved CVs can be edited from **Tailored CVs**. Editing starts with the saved section content and order, lets you tailor wording without changing the reusable library, add current library sections, and regenerate the Markdown and PDF. Editing a section's wording inside a CV detaches that block from future library updates. The CV keeps its original contact-detail snapshot.

Use **Tree View** when you want to customize the whole saved snapshot in one place. The CV is the root; personal details and sections sit beneath it; and each section owns its entry lines and bullet points. Double-click values to edit them, add or remove section content, reorder sibling sections or lines, then choose **Save & export**. Reordering a linked section preserves its library link, while changing that section's title, category, or content makes it CV-specific.

Editing a section in **Section Library** updates every CV that still uses that library section and regenerates their Markdown and PDF exports. If updated content no longer fits legibly on one page, the CV remains updated and the app reports which export needs attention.

Select any application, CV, or library section to see its complete details below the table, including notes and posting URLs, linked applications, saved contact information, export paths, and full section content.

### Import an existing CV

In **Section Library**, choose **Import CV** and select a PDF, Markdown, or text CV. The importer recognizes standard headings such as Education, Experience, Projects, and Skills; preserves bullets; and converts right-hand date/location columns into the app's export syntax. Review the detected sections before saving them. Updating Personal Details is opt-in, so importing a CV does not overwrite your current profile by default.

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

## Safari capture handoff

The **Safari Capture** page starts a local, authenticated endpoint for a future Safari Web Extension. It binds only to `127.0.0.1`; copy the endpoint and token into the extension's settings. The extension must send:

```http
POST /v1/applications
Content-Type: application/json
X-CV-Manager-Token: <token shown by the app>
```

```json
{
  "company": "Example Co.",
  "role": "Data Engineer",
  "location": "Montreal, QC",
  "posting_url": "https://example.com/careers/123",
  "notes": "Optional note"
}
```

`company` and `role` are required. Captured records enter the tracker as **Applied** and can then be linked to the CV you used.

### Build the Safari extension

The companion source lives in [`safari-extension`](safari-extension). On a Mac with Xcode installed, create a Safari Web Extension App and replace its extension resources with that folder's contents (or use Xcode's Safari Web Extension conversion flow). Then:

1. Open **Safari Capture** in CV Manager and click **Start capture bridge**.
2. Copy the displayed endpoint and token into the extension's **Connection settings**.
3. On a job page, click the Safari toolbar button, fill the company and role, then save.

The popup automatically includes the active tab's URL. It never sends data to a remote service; it communicates only with CV Manager on `127.0.0.1`.
