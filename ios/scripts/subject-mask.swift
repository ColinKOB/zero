// Writes a grayscale subject mask for each image, using Apple's Vision subject lifting (the same as
// "Copy Subject" in Photos). Used by make-agent-assets.py; run with: swift subject-mask.swift in.png out.png ...
import CoreImage
import Foundation
import Vision

let args = Array(CommandLine.arguments.dropFirst())
precondition(args.count % 2 == 0, "pairs of input and output paths")
let context = CIContext()
for i in stride(from: 0, to: args.count, by: 2) {
    let input = URL(fileURLWithPath: args[i]), output = URL(fileURLWithPath: args[i + 1])
    let handler = VNImageRequestHandler(url: input)
    let request = VNGenerateForegroundInstanceMaskRequest()
    try handler.perform([request])
    guard let result = request.results?.first else { print("no subject: \(args[i])"); continue }
    let mask = try result.generateScaledMaskForImage(forInstances: result.allInstances, from: handler)
    let image = CIImage(cvPixelBuffer: mask)
    try context.writePNGRepresentation(of: image, to: output, format: .L8, colorSpace: CGColorSpaceCreateDeviceGray())
    print("ok: \(output.lastPathComponent)")
}
