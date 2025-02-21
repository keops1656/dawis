from database.connection import Connection
from service.alerting import Alert, AlertQueue
from service.email import Dispatcher, DispatcherException
from utilities.configuration import Configuration
from utilities.exceptions import ConfigurationMissingError, ConfigurationInvalidError
from datetime import timedelta
from os import linesep
from tempfile import NamedTemporaryFile
from time import time


class _DataAlreadyExistError(Exception):
    pass


class AlertingDispatcher:
    def __init__(self, configuration: Configuration, configuration_key: str, connection: Connection):
        self.configuration = configuration
        self.module_configuration = configuration.aggregations.get_custom_configuration_aggregation(configuration_key)
        self.connection = connection
        self.mongodb = connection.mongodb
        self.bigquery = None
        self.alert_queue = AlertQueue(self.mongodb)

    def run(self):
        print('Running Alerting Dispatcher Module:')
        timer_run = time()

        if 'bigquery' == self.module_configuration.database:
            self.bigquery = self.connection.bigquery

        if 'configurations' in self.module_configuration.settings and \
            type(self.module_configuration.settings['configurations']) is list:
            for configuration in self.module_configuration.settings['configurations']:
                try:
                    self._process_configuration(configuration)
                    print(' - OK')
                except _DataAlreadyExistError:
                    print(' - EXISTS')

        print('\ncompleted: {:s}'.format(str(timedelta(seconds=int(time() - timer_run)))))

    def _process_configuration(self, configuration):
        if 'type' in configuration and type(configuration['type']) is str:
            alert_type = configuration['type']
        else:
            raise ConfigurationMissingError('Missing "type" for alert dispatch configuration')

        if 'email' == alert_type:
            self._process_email_configuration(configuration)
        else:
            raise ConfigurationInvalidError('Invalid alert type "{}"'.format(alert_type))

    def _process_email_configuration(self, configuration):
        template_variables = {}
        template_text_path = None
        template_html_path = None

        if 'smtp' in configuration and type(configuration['smtp']) is dict:
            smtp_configuration = configuration['smtp']
            if 'host' in smtp_configuration and type(smtp_configuration['host']) is str:
                host = smtp_configuration['host']
            else:
                raise ConfigurationMissingError('Missing host in alert mail smtp configuration')

            if 'port' in smtp_configuration and type(smtp_configuration['port']) is int:
                port = smtp_configuration['port']
            else:
                raise ConfigurationMissingError('Missing port in alert mail smtp configuration')

            if 'user' in smtp_configuration and type(smtp_configuration['user']) is str:
                user = smtp_configuration['user']
            else:
                raise ConfigurationMissingError('Missing user in alert mail smtp configuration')

            if 'password' in smtp_configuration and type(smtp_configuration['password']) is str:
                password = smtp_configuration['password']
            else:
                raise ConfigurationMissingError('Missing user in alert mail smtp configuration')

            encryption = None

            if 'encryption' in smtp_configuration and type(smtp_configuration['encryption']) is str:
                encryption = smtp_configuration['encryption']
        else:
            raise ConfigurationMissingError('Missing smtp configuration')

        if 'subject' in configuration and type(configuration['subject']) is str:
            subject = configuration['subject']
        else:
            raise ConfigurationMissingError('Missing subject in alert configuration')

        if 'fromEmail' in configuration and type(configuration['fromEmail']) is str:
            from_email = configuration['fromEmail']
        else:
            raise ConfigurationMissingError('Missing from email in alert configuration')

        if 'toEmail' in configuration and (
            type(configuration['toEmail']) is str or type(configuration['toEmail']) is list
        ):
            to_email = configuration['toEmail']
        else:
            raise ConfigurationMissingError('Missing to email in alert configuration')

        if 'templateHtml' in configuration and type(configuration['templateHtml']) is str:
            template_html_path = configuration['templateHtml']

        if 'templateText' in configuration and type(configuration['templateText']) is str:
            template_text_path = configuration['templateText']

        if template_html_path is None and template_text_path is None:
            raise ConfigurationMissingError('You at least have to provide a html or text email template')

        if 'templateVariables' in configuration and type(configuration['templateVariables']) is dict:
            template_variables = configuration['templateVariables']

        if 'groups' in configuration and type(configuration['groups']) is list:
            groups = configuration['groups']
        else:
            raise ConfigurationMissingError('Missing groups to fetch alerts for')

        alerts = self.alert_queue.fetch_alerts(groups)

        if 0 < len(alerts):
            template_variables['alerts'] = alerts

            with NamedTemporaryFile(mode='w+t', suffix='.log') as log_file:
                for alert in alerts:
                    log_item = '['
                    log_item += alert.date.isoformat()
                    log_item += '] '
                    log_item += alert.message

                    if type(alert.data) is dict and 0 < len(alert.data):
                        log_item += ' | '
                        log_item += str(alert.data)

                    log_file.write(log_item + linesep)

                log_file.flush()

                try:
                    with Dispatcher(host, port, user, password, encryption) as dispatcher:
                        if template_html_path is None:
                            dispatcher.send_text_email(
                                from_email,
                                to_email,
                                subject,
                                template_text_path,
                                template_variables,
                                {'alerts.log': log_file.name}
                            )
                        elif template_text_path is None:
                            dispatcher.send_html_email(
                                from_email,
                                to_email,
                                subject, template_html_path,
                                template_variables,
                                {'alerts.log': log_file.name}
                            )
                        else:
                            dispatcher.send_email(
                                from_email,
                                to_email,
                                subject,
                                template_html_path,
                                template_text_path,
                                template_variables,
                                {'alerts.log': log_file.name}
                            )
                except (ConnectionError, DispatcherException) as error:
                    self.alert_queue.add_alerts(alerts)
                    raise ConfigurationInvalidError(str(error))
