# frozen_string_literal: true

require "ipaddr"
require "yaml"

ROOT = File.expand_path(__dir__)
definition_selection = ENV["TESTBED"]
abort "TESTBED must select an explicit testbed definition" if definition_selection.nil? || definition_selection.strip.empty?
definition_path = definition_selection
definition_path = File.expand_path(definition_path, ROOT)
abort "testbed definition not found: #{definition_path}" unless File.file?(definition_path)

testbed = YAML.safe_load(File.read(definition_path), aliases: false)
abort "unsupported testbed schema" unless testbed["schemaVersion"] == 1

component_lock_path = File.join(ROOT, "components.lock.yaml")
component_lock = YAML.safe_load(File.read(component_lock_path), aliases: false)
abort "unsupported component lock schema" unless component_lock["schemaVersion"] == 1
component_revisions = component_lock.fetch("components").to_h do |component|
  [component.fetch("path"), component.fetch("commit")]
end

component_path_for_service = lambda do |service|
  case service
  when "mongodb" then nil
  when "nrf", "adrf"
    "NFs/#{service}"
  when /^nwdaf-/ then "NFs/nwdaf"
  else abort "unsupported Guest service for component identity: #{service}"
  end
end

requested_provider = ENV["VAGRANT_DEFAULT_PROVIDER"]
abort "unsupported provider #{requested_provider}; expected virtualbox" if requested_provider && requested_provider != "virtualbox"
provider_name = "virtualbox"

machines = testbed.fetch("machines")
networks = testbed.fetch("networks")
abort "machines must be a non-empty mapping" unless machines.is_a?(Hash) && !machines.empty?
machines.each_key do |machine_name|
  abort "invalid machine name: #{machine_name}" unless machine_name.match?(/\A[a-z0-9][a-z0-9-]*\z/)
end
placement = testbed.fetch("placement")
abort "placement machine inventory must exactly match machines" unless \
  (placement.keys - ["host-containers"]).sort == machines.keys.sort

machines.each do |machine_name, machine|
  machine.fetch("interfaces").each do |network_name, address|
    network = networks.fetch(network_name) { abort "unknown network #{network_name}" }
    cidr = IPAddr.new(network.fetch("cidr"))
    abort "#{machine_name} #{address} is outside #{network_name}" unless cidr.include?(IPAddr.new(address))
  end
end

Vagrant.configure("2") do |config|
  config.vm.box = testbed.dig("guest", "box") || "ubuntu/jammy64"
  config.vm.box_version = testbed.dig("guest", "boxVersion") if testbed.dig("guest", "boxVersion")
  config.vm.box_check_update = false
  config.vm.synced_folder ".", "/vagrant", disabled: true
  config.vm.synced_folder ROOT, "/opt/5g-nwdaf-infrastructure/source",
    type: "rsync", rsync__auto: false,
    rsync__exclude: [
      ".git/", ".vagrant/", ".generated/", "ML/", "config/local/",
      "**/.venv/", "**/__pycache__/", "**/.pytest_cache/", "**/node_modules/"
    ]

  machines.each do |machine_name, machine|
    guest_services = placement.fetch(machine_name)
    abort "placement.#{machine_name} must be non-empty" unless guest_services.is_a?(Array) && !guest_services.empty?
    provision_inventory = guest_services.join(",")
    revision_inventory = guest_services.filter_map { |service| component_path_for_service.call(service) }
      .uniq.sort.map do |path|
        "#{path}=#{component_revisions.fetch(path) { abort "component lock omits #{path}" }}"
      end.join(",")
    abort "component revision inventory is empty for #{machine_name}" if revision_inventory.empty?
    config.vm.define machine_name do |node|
      node.vm.hostname = "5g-nwdaf-#{machine_name}"

      machine.fetch("interfaces").each do |_network_name, address|
        node.vm.network "private_network", ip: address
      end

      resources = machine.fetch("resources")
      node.vm.disk :disk, size: "#{resources.fetch("diskGiB")}GB", primary: true
      node.vm.provider "virtualbox" do |vb|
        vb.name = "5g-nwdaf-#{machine_name}"
        vb.memory = resources.fetch("memoryMiB")
        vb.cpus = resources.fetch("cpus")
      end
      node.vm.provision "shell", path: "scripts/guest/common.sh",
        args: [machine_name], run: "once"
      if guest_services.include?("mongodb")
        node.vm.provision "shell", path: "scripts/guest/core.sh",
          args: ["setup", provision_inventory, revision_inventory], run: "once"
      else
        node.vm.provision "shell", path: "scripts/guest/path.sh",
          args: [machine_name, "setup", provision_inventory, revision_inventory], run: "once"
      end

    end
  end

  config.vm.provider(provider_name) {} if provider_name
end
