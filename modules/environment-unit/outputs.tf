output "resource_id" {
  value = random_id.env.hex
}

output "manifest_path" {
  value = local_file.manifest.filename
}
