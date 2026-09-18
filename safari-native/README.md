# Native Safari host

Full Xcode is required; Apple Command Line Tools alone do not include the Safari extension SDK.

## Run on macOS

1. Open `generated/CV Manager Safari/CV Manager Safari.xcodeproj` in Xcode.
2. Select the **CV Manager Safari (macOS)** scheme. Configure signing for both macOS targets with a team that has the `group.com.cvmanager.app` App Group.
3. Build and run the containing app once. The project references the web resources in `../safari-extension`, so rebuild after changing them.
4. In **Safari → Settings → Extensions**, enable **CV Manager Capture** and grant website access.
5. If the extension is enabled but its toolbar button is missing for a local development build, turn on **Safari → Settings → Developer → Allow unsigned extensions**, then uncheck and recheck **CV Manager Capture** in Extensions settings. Safari resets the developer setting when it quits, so repeat this after restarting Safari if needed.

The repository also contains standalone Swift handler and entitlement files for creating another host project. The checked-in Xcode project already includes them.

For production packaging, sign the CV Manager desktop app with access to the same App Group. During local Python development, the app uses the corresponding folder in `~/Library/Group Containers`. Set `CV_MANAGER_SAFARI_BRIDGE_DIR` to an alternate directory for isolated development or tests.
