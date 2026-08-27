# Native Safari host

The files in this directory replace the generated native portion of a Safari Web Extension project. Full Xcode is required; Apple Command Line Tools alone do not include the converter or Safari extension SDK.

## Create the host project

1. In Xcode, create a macOS Safari Web Extension App, or run Apple's Safari Web Extension converter against `../safari-extension`.
2. Use `com.cvmanager.app.safari` for the containing app bundle identifier and a child identifier for the extension target.
3. Replace the generated extension resources with the contents of `../safari-extension`.
4. Replace the generated `SafariWebExtensionHandler.swift` with the file in this directory and include it only in the native extension target.
5. Assign `CVManagerSafari.entitlements` to the app target and `CVManagerSafariExtension.entitlements` to the extension target.
6. Register `group.com.cvmanager.app` in the signing team and enable that App Group on both targets.
7. Build and run the containing app once, then enable the extension in **Safari → Settings → Extensions** and grant website access.

For production packaging, sign the CV Manager desktop app with access to the same App Group. During local Python development, the app uses the corresponding folder in `~/Library/Group Containers`. Set `CV_MANAGER_SAFARI_BRIDGE_DIR` to an alternate directory for isolated development or tests.
