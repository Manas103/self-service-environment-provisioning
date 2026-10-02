variable "environment_name" {
  type = string
}

variable "region" {
  type = string
}

variable "ttl_hours" {
  type = number
}

variable "owner" {
  type = string
}

variable "cost_center" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
