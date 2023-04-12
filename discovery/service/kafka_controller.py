import sys

from discovery.service.service import AbstractPropertyBuilder
from discovery.utils.constants import DEFAULT_KEY
from discovery.utils.inventory import CPInventoryManager
from discovery.utils.services import ConfluentServices, ServiceData
from discovery.utils.utils import InputContext, Logger, FileUtils

logger = Logger.get_logger()

class_name = ""
gl_host_service_properties = ""

class KafkaControllerServicePropertyBuilder:

    @staticmethod
    def build_properties(input_context: InputContext, inventory: CPInventoryManager):
        from discovery.service import get_service_builder_class
        builder_class = get_service_builder_class(modules=sys.modules[__name__],
                                                  default_class_name="KafkaControllerServicePropertyBaseBuilder",
                                                  version=input_context.from_version)
        global class_name
        class_name = builder_class
        builder_class(input_context, inventory).build_properties()


class KafkaControllerServicePropertyBaseBuilder(AbstractPropertyBuilder):
    inventory = None
    input_context = None
    hosts = []

    def __init__(self, input_context: InputContext, inventory: CPInventoryManager):
        self.inventory = inventory
        self.input_context = input_context
        self.mapped_service_properties = set()
        self.service = ConfluentServices(input_context).KAFKA_CONTROLLER()
        self.group = self.service.group

    def build_properties(self):
        # Get the hosts for given service
        hosts = self.get_service_host(self.service, self.inventory)
        self.hosts = hosts

        if not hosts:
            logger.error(f"Could not find any host with service {self.service.name} ")
            return

        host_service_properties = self.get_property_mappings(self.input_context, self.service, hosts)
        global gl_host_service_properties
        gl_host_service_properties = host_service_properties
        service_properties = host_service_properties.get(hosts[0]).get(DEFAULT_KEY)

        # Build service user group properties
        self.__build_daemon_properties(self.input_context, self.service, hosts)

        # Build service properties
        self.__build_service_properties(service_properties)

        # Add custom properties
        self.__build_custom_properties(host_service_properties, self.mapped_service_properties)

        # Build Command line properties
        self.__build_runtime_properties(hosts)

    def __build_daemon_properties(self, input_context: InputContext, service: ServiceData, hosts: list):

        response = self.get_service_user_group(input_context, service, hosts)
        self.update_inventory(self.inventory, response)

    def __build_service_properties(self, service_properties):

        for key, value in vars(class_name).items():
            if callable(getattr(class_name, key)) and key.startswith("_build"):
                func = getattr(class_name, key)
                logger.info(f"Calling Kafka Controller property builder.. {func.__name__}")
                result = func(self, service_properties)
                self.update_inventory(self.inventory, result)

    def __build_custom_properties(self, host_service_properties: dict, mapped_properties: set):

        custom_group = "kafka_controller_custom_properties"
        skip_properties = set(FileUtils.get_kafka_controller_configs("skip_properties"))

        # Get host server properties dictionary
        _host_service_properties = dict()
        for host in host_service_properties.keys():
            _host_service_properties[host] = host_service_properties.get(host).get(DEFAULT_KEY)
        self.build_custom_properties(inventory=self.inventory, group=self.group,
                                     custom_properties_group_name=custom_group,
                                     host_service_properties=_host_service_properties, skip_properties=skip_properties,
                                     mapped_properties=mapped_properties)

    def __build_runtime_properties(self, hosts: list):
        # Build Java runtime overrides
        data = (self.group, {
            'kafka_controller_custom_java_args': self.get_jvm_arguments(self.input_context, self.service, hosts)
        })
        self.update_inventory(self.inventory, data)

    def __get_user_dict(self, service_prop: dict, key: str) -> dict:
        users = dict()
        jaas_config = service_prop.get(key, None)
        if not jaas_config:
            return users

        # parse line to get the user configs
        for token in jaas_config.split():
            if "=" in token:
                username, password = token.split('=')
                if username.startswith('user_'):
                    principal = username.replace('user_', '')
                    # Sanitize the password
                    password = password.rstrip(';').strip('"')
                    users[principal] = {'principal': principal, 'password': password}

        return users

    def _build_replication_factors(self, service_properties: dict) -> tuple:

        property_dict = dict()
        key = "confluent.balancer.topic.replication.factor"
        value = service_properties.get(key)

        self.mapped_service_properties.add(key)
        self.mapped_service_properties.add("confluent.license.topic.replication.factor")
        self.mapped_service_properties.add("confluent.metadata.topic.replication.factor")
        if value is not None:
            property_dict["kafka_controller_default_internal_replication_factor"] = int(value)

        # Check for audit logs replication factor
        key = "confluent.security.event.logger.exporter.kafka.topic.replicas"
        audit_replication = service_properties.get(key)
        if value is not None and audit_replication is not None and value != audit_replication:
            property_dict["audit_logs_destination_enabled"] = True
            self.mapped_service_properties.add(key)

        return self.group, property_dict

    def _build_inter_broker_listener_name(self, service_prop: dict) -> tuple:
        key = "inter.broker.listener.name"
        self.mapped_service_properties.add(key)
        return self.group, {"kafka_broker_inter_broker_listener_name": service_prop.get(key).lower()}

    def _build_service_metrics(self, service_prop: dict) -> tuple:
        key = "confluent.metrics.reporter.bootstrap.servers"
        self.mapped_service_properties.add(key)
        metric_reporter_enabled = key in service_prop
        return self.group, {"kafka_controller_metrics_reporter_enabled": metric_reporter_enabled}

    def _build_schema_registry_url(self, service_prop: dict) -> tuple:
        key = "confluent.schema.registry.url"
        self.mapped_service_properties.add(key)
        schema_registry_url = key in service_prop
        return self.group, {"kafka_broker_schema_validation_enabled": schema_registry_url}

    def _build_ssl_properties(self, service_properties: dict) -> tuple:

        property_dict = dict()
        property_list = ["ssl.key.password", "ssl.keystore.location", "ssl.keystore.password", "ssl.truststore.location","ssl.truststore.password"]

        for property_key in property_list:
            self.mapped_service_properties.add(property_key)

        kafka_controller_ssl_enabled = service_properties.get('ssl.keystore.location')

        if kafka_controller_ssl_enabled is None:
            return self.group, {}

        property_dict['ssl_enabled'] = True
        property_dict['ssl_provided_keystore_and_truststore'] = True
        property_dict['ssl_provided_keystore_and_truststore_remote_src'] = True
        property_dict['ssl_truststore_ca_cert_alias'] = ''
        property_dict['kafka_controller_pkcs12_truststore_path'] = service_properties.get('ssl.truststore.location')
        property_dict['kafka_controller_pkcs12_keystore_path'] = service_properties.get('ssl.keystore.location')
        property_dict['ssl_keystore_store_password'] = service_properties.get('.ssl.keystore.password')
        property_dict['ssl_keystore_key_password'] = service_properties.get('.ssl.key.password')
        property_dict['ssl_truststore_password'] = service_properties.get('ssl.truststore.password')

        keystore_aliases = self.get_keystore_alias_names(input_context=self.input_context,
                                                         keystorepass=property_dict['kafka_controller_keystore_storepass'],
                                                         keystorepath=property_dict['kafka_controller_keystore_path'],
                                                         hosts=self.hosts)
        truststore_aliases = self.get_keystore_alias_names(input_context=self.input_context,
                                                           keystorepass=property_dict['kafka_controller_truststore_storepass'],
                                                           keystorepath=property_dict['kafka_controller_truststore_path'],
                                                           hosts=self.hosts)
        if keystore_aliases:
            # Set the first alias name
            property_dict["ssl_keystore_alias"] = keystore_aliases[0]
        if truststore_aliases:
            property_dict["ssl_truststore_ca_cert_alias"] = truststore_aliases[0]

        return "kafka_controller", property_dict

    def _build_mtls_properties(self, service_properties: dict) -> tuple:
        kafka_controller_client_authentication_type = service_properties.get('listener.name.controller.ssl.client.auth')
        if kafka_controller_client_authentication_type == 'required':
            return "kafka_controller", {'ssl_mutual_auth_enabled': True}

        return self.group, {}

    def _build_fips_properties(self, service_properties: dict) -> tuple:
        key = 'enable.fips'
        self.mapped_service_properties.add(key)
        property_dict = dict()
        property_dict['fips_enabled'] = bool(service_properties.get(key, False))
        if property_dict['fips_enabled'] is True:
            property_dict['kafka_controller_bcfks_keystore_path'] = service_properties.get('ssl.keystore.location')
            property_dict['kafka_controller_bcfks_truststore_path'] = service_properties.get('ssl.truststore.location')

            self.mapped_service_properties.add("ssl.keymanager.algorithm")
            self.mapped_service_properties.add("ssl.trustmanager.algorithm")
            self.mapped_service_properties.add("ssl.keystore.type")
            self.mapped_service_properties.add("ssl.truststore.type")
            self.mapped_service_properties.add("ssl.truststore.location")
            self.mapped_service_properties.add("ssl.truststore.password")
            self.mapped_service_properties.add("ssl.keystore.location")
            self.mapped_service_properties.add("ssl.keystore.password")
            self.mapped_service_properties.add("ssl.key.password")
            self.mapped_service_properties.add("ssl.enabled.protocols")
            self.mapped_service_properties.add("confluent.metadata.server.http2.enabled")
            self.mapped_service_properties.add("confluent.metrics.reporter.ssl.keymanager.algorithm")
            self.mapped_service_properties.add("confluent.metrics.reporter.ssl.keystore.type")
            self.mapped_service_properties.add("confluent.metrics.reporter.ssl.trustmanager.algorithm")
            self.mapped_service_properties.add("confluent.metrics.reporter.ssl.truststore.type")

        # Ignore generated ssl listener properties
        listeners = self.__get_all_listeners(service_prop=service_properties)

        for listener in listeners:
            from urllib.parse import urlparse
            parsed_uri = urlparse(listener)
            name = parsed_uri.scheme
            self.mapped_service_properties.add(f"listener.name.{name}.ssl.enabled.protocols")
            self.mapped_service_properties.add(f"listener.name.{name}.ssl.keymanager.algorithm")
            self.mapped_service_properties.add(f"listener.name.{name}.ssl.keystore.type")
            self.mapped_service_properties.add(f"listener.name.{name}.ssl.trustmanager.algorithm")
            self.mapped_service_properties.add(f"listener.name.{name}.ssl.truststore.type")

        return "all", property_dict

    def __get_all_listeners(self, service_prop: dict) -> list:
        key = "listeners"
        self.mapped_service_properties.add(key)
        return service_prop.get(key).split(",")

    def _build_rbac_properties(self, service_prop: dict) -> tuple:
        property_dict = dict()
        key1 = 'authorizer.class.name'
        key2 = 'super.users'
        if service_prop.get(key1) != 'io.confluent.kafka.security.authorizer.ConfluentServerAuthorizer':
            return self.group, {}

        self.mapped_service_properties.add(key1)
        self.mapped_service_properties.add(key2)
        property_dict['rbac_enabled'] = True

        # Update Ldap username and password only if secrete protection is not enabled.
        if self.get_secret_protection_master_key(self.input_context, self.service, self.hosts):
            property_dict['kafka_controller_ldap_user'] = "<<Value has been encrypted using secrets protection>>"
            property_dict['kafka_controller_ldap_password'] = "<<Value has been encrypted using secrets protection>>"
        else:
            property_dict['kafka_controller_ldap_user'] = metadata_user_info.split(':')[0]
            property_dict['kafka_controller_ldap_password'] = metadata_user_info.split(':')[1]

        return self.group, property_dict

    def _build_mds_properties(self, service_prop: dict) -> tuple:
        if ('rbac_enabled' not in self.inventory.groups.get('kafka_controller').vars) or \
                ('rbac_enabled' in self.inventory.groups.get('kafka_controller').vars and
                 self.inventory.groups.get('kafka_controller').vars.get('rbac_enabled') is False):
            return "all", {}
        key1 = 'super.users'
        property_dict = dict()
        super_user_property = service_prop.get(key1)
        property_dict['create_mds_certs'] = False
        property_dict['mds_super_user'] = super_user_property.split(";")[0].split('User:', 1)[1]
        ldap_principal = service_prop.get('ldap.java.naming.security.principal', None)
        if ldap_principal is not None:
            try:
                tmp_super_user = ldap_principal.split("uid=", 1)[1].split(",")[0].strip()
            except IndexError as e:
                tmp_super_user = ""
            if tmp_super_user != "":
                property_dict['mds_super_user'] = tmp_super_user

        property_dict['mds_super_user_password'] = ''

        key2 = 'confluent.metadata.bootstrap.servers'
        key3 = 'confluent.metadata.server.kraft.controller.enabled'
        if service_prop.get(key3) is not None:
            property_dict['external_mds_enabled'] = True

        property_dict['mds_broker_bootstrap_servers'] = service_prop.get(key2)

        self.mapped_service_properties.add(key2)
        self.mapped_service_properties.add(key3)

        return self.group, property_dict

    def _build_secret_protection_key(self, service_prop: dict) -> tuple:
        master_key = self.get_secret_protection_master_key(self.input_context, self.service, self.hosts)
        if master_key:
            return self.group, {
                'secrets_protection_enabled': True,
                'secrets_protection_masterkey': master_key,
                'regenerate_masterkey': False
            }
        else:
            return self.group, {}

    def _build_telemetry_properties(self, service_prop: dict) -> tuple:
        property_dict = self.build_telemetry_properties(service_prop)
        return self.group, property_dict

    def _build_jmx_properties(self, service_properties: dict) -> tuple:
        monitoring_details = self.get_monitoring_details(self.input_context, self.service, self.hosts, 'KAFKA_OPTS')
        service_monitoring_details = dict()

        for key, value in monitoring_details.items():
            service_monitoring_details[f"{self.group}_{key}"] = value

        return self.group, service_monitoring_details

    def _build_audit_log_properties(self, service_prop: dict) -> tuple:
        global gl_host_service_properties
        key = "confluent.security.event.logger.exporter.kafka.bootstrap.servers"
        
        self.mapped_service_properties.add(key)
        for hostname, properties in gl_host_service_properties.items():
            default_properties = properties.get(DEFAULT_KEY)
            if key in default_properties:
                host = self.inventory.get_host(hostname)
                host.set_variable('audit_logs_destination_enabled', True)
                host.set_variable('audit_logs_destination_bootstrap_servers', default_properties.get(key))
                cluster, principal_name = self.get_audit_log_properties(input_context=self.input_context,
                                                                        hosts=hostname, mds_user='mds',
                                                                        mds_password='password')
                host.set_variable('audit_logs_destination_kafka_cluster_name', cluster['clusterName'])
                host.set_variable('audit_logs_destination_principal', principal_name)

        return 'all', {}

    def _build_log4j_properties(self, service_properties: dict) -> tuple:
        log4j_file = self.get_log_file_path(self.input_context, self.service, self.hosts, "KAFKA_LOG4J_OPTS")
        default_log4j_file = "etc/kafka/log4j.properties"
        root_logger, file = self.get_root_logger(self.input_context, self.hosts, log4j_file, default_log4j_file)

        if root_logger is None or file is None:
            return self.group, {'kafka_controller_custom_log4j': False}

        return self.group, {
            'log4j_file': file,
            'kafka_controller_log4j_root_logger': root_logger
        }

    def _build_kerberos_properties(self, service_prop: dict) -> tuple:
        key1 = 'listener.name.controller.gssapi.sasl.jaas.config'
        
        self.mapped_service_properties.add(key1)

        sasl_config = ""
        if service_prop.get(key1) is not None:
            sasl_config = service_prop.get(key1)
        else:
            return self.group, {}

        try:
            keytab = sasl_config.split('keyTab="')[1].split('"')[0]
            principal = sasl_config.split('principal="')[1].split('"')[0]
        except IndexError as e:
            keytab = ""
            principal = ""
        if keytab != "" or principal != "":
            return self.group, {
                'sasl_protocol': 'kerberos',
                'kafka_controller_kerberos_principal': principal,
                'kafka_controller_kerberos_keytab_path': keytab
            }
        return 'all', {}

    def _build_kerberos_configurations(self, service_prop: dict) -> tuple:
        property_dict = dict()
        if 'sasl_protocol' in self.inventory.groups.get('kafka_controller').vars and \
                self.inventory.groups.get('kafka_controller').vars.get('sasl_protocol') == 'kerberos':
            kerberos_config_file = '/etc/krb5.conf'
            realm, kdc, admin = self.get_kerberos_configurations(self.input_context, self.hosts, kerberos_config_file)
            kerberos_config = {
                'realm': realm,
                'kdc_hostname': kdc,
                'admin_hostname': admin
            }
            property_dict['kerberos_configure'] = False
            property_dict['kerberos'] = kerberos_config
        return "all", property_dict


class KafkaControllerServicePropertyBaseBuilder74(KafkaControllerServicePropertyBaseBuilder):
    pass
