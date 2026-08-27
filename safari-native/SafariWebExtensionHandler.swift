import Foundation
import SafariServices

private let appGroupIdentifier = "group.com.cvmanager.app"
private let maximumCVSize = 5 * 1024 * 1024

final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        do {
            guard
                let item = context.inputItems.first as? NSExtensionItem,
                let message = item.userInfo?[SFExtensionMessageKey] as? [String: Any],
                let operation = message["operation"] as? String
            else {
                throw BridgeError.invalidMessage
            }

            let value: [String: Any]
            switch operation {
            case "ping":
                _ = try bridgeRoot()
                value = ["ok": true]
            case "list_cvs":
                value = try listCVs()
            case "get_cv":
                value = try getCV(identifier: integer(message["cv_id"]))
            case "write_event":
                guard let request = message["request"] as? [String: Any] else {
                    throw BridgeError.invalidMessage
                }
                value = try writeEvent(request)
            default:
                throw BridgeError.unknownOperation
            }
            complete(context, value)
        } catch {
            complete(context, ["ok": false, "error": error.localizedDescription])
        }
    }

    private func bridgeRoot() throws -> URL {
        guard let group = FileManager.default.containerURL(
            forSecurityApplicationGroupIdentifier: appGroupIdentifier
        ) else {
            throw BridgeError.missingAppGroup
        }
        let root = group
            .appendingPathComponent("Library/Application Support/CV Manager", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: nil)
        return root
    }

    private func catalog() throws -> [[String: Any]] {
        let data = try Data(contentsOf: bridgeRoot().appendingPathComponent("catalog.json"))
        guard
            let document = try JSONSerialization.jsonObject(with: data) as? [String: Any],
            let cvs = document["cvs"] as? [[String: Any]]
        else {
            throw BridgeError.invalidCatalog
        }
        return cvs
    }

    private func listCVs() throws -> [String: Any] {
        return ["ok": true, "cvs": try catalog()]
    }

    private func getCV(identifier: Int?) throws -> [String: Any] {
        guard
            let identifier,
            let cv = try catalog().first(where: { integer($0["id"]) == identifier }),
            let filename = cv["filename"] as? String,
            URL(fileURLWithPath: filename).lastPathComponent == filename,
            filename.lowercased().hasSuffix(".pdf")
        else {
            throw BridgeError.missingCV
        }
        let data = try Data(contentsOf: bridgeRoot().appendingPathComponent("cvs", isDirectory: true).appendingPathComponent(filename))
        guard data.count <= maximumCVSize else { throw BridgeError.cvTooLarge }
        var response = cv
        response["ok"] = true
        response["data"] = data.base64EncodedString()
        return response
    }

    private func writeEvent(_ request: [String: Any]) throws -> [String: Any] {
        guard
            integer(request["version"]) == 1,
            let eventID = safeIdentifier(request["event_id"]),
            let requestID = safeIdentifier(request["request_id"]),
            let state = request["state"] as? String,
            ["active", "cancelled"].contains(state),
            JSONSerialization.isValidJSONObject(request)
        else {
            throw BridgeError.invalidEvent
        }
        let requests = try bridgeRoot().appendingPathComponent("requests", isDirectory: true)
        try FileManager.default.createDirectory(at: requests, withIntermediateDirectories: true, attributes: nil)
        let timestamp = Int(Date().timeIntervalSince1970 * 1_000)
        let destination = requests.appendingPathComponent("\(timestamp)-\(eventID)-\(requestID).json")
        let data = try JSONSerialization.data(withJSONObject: request, options: [.sortedKeys])
        try data.write(to: destination, options: [.atomic])
        return ["ok": true]
    }

    private func integer(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        return nil
    }

    private func safeIdentifier(_ value: Any?) -> String? {
        guard let value = value as? String, !value.isEmpty, value.count <= 100 else { return nil }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        return value.unicodeScalars.allSatisfy(allowed.contains) ? value : nil
    }

    private func complete(_ context: NSExtensionContext, _ message: [String: Any]) {
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: message]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}

private enum BridgeError: LocalizedError {
    case invalidMessage, unknownOperation, missingAppGroup, invalidCatalog, missingCV, cvTooLarge, invalidEvent

    var errorDescription: String? {
        switch self {
        case .invalidMessage: return "The Safari message was invalid."
        case .unknownOperation: return "The Safari operation is not supported."
        case .missingAppGroup: return "The shared CV Manager folder is unavailable."
        case .invalidCatalog: return "Open CV Manager once to publish its CV catalog."
        case .missingCV: return "That CV is no longer available."
        case .cvTooLarge: return "That CV is too large to attach from Safari."
        case .invalidEvent: return "The application log was invalid."
        }
    }
}
