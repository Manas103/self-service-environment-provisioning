terraform {
  required_providers {
    local  = { source = "hashicorp/local", version = "~> 2.5" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

resource "random_id" "env" {
  byte_length = 4
  keepers = {
    environment_name = var.environment_name
  }
}

resource "local_file" "manifest" {
  filename = "${path.module}/../../artifacts/${var.environment_name}.json"
  content = jsonencode({
    environment_name = var.environment_name
    region           = var.region
    ttl_hours        = var.ttl_hours
    owner            = var.owner
    cost_center      = var.cost_center
    tags             = var.tags
    resource_id      = random_id.env.hex
  })
}
